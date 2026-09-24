import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db import Database
from bot.quota import tavily_exhausted
from bot.services.posting import _sources_relevant
from bot.handlers.common import SearchStates, _report_error, _safe_delete
from bot.handlers.post_actions import PostActions
from bot.handlers.pipeline import run_topic_pipeline
from bot.services.llm import LLMError, LLMProvider
from bot.services.posting import _deliver_post, _generate_post
from bot.services.search import SearchError, SearchProvider

log = logging.getLogger(__name__)
router = Router()


class TopicStates(StatesGroup):
    waiting_topic = State()


@router.message(Command("topic"))
async def cmd_topic(message: Message, state: FSMContext) -> None:
    await state.set_state(TopicStates.waiting_topic)
    await state.update_data(since=message.date.timestamp())
    await message.answer(
        "Напиши тему поста — я изучу информацию и подготовлю пост."
    )


@router.message(
    TopicStates.waiting_topic, F.text.func(lambda t: bool(t) and not t.startswith("/"))
)
async def process_topic(
    message: Message,
    state: FSMContext,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
) -> None:
    _guard = (await state.get_data()).get("since")
    if _guard and message.date.timestamp() < _guard:
        return
    query = message.text.strip()
    await run_topic_pipeline(message, state, query, search, llm, db, db_user)


@router.callback_query(
    StateFilter(SearchStates.choosing_topic, PostActions.ready),
    F.data.startswith("topic:"),
)
async def choose_topic(
    callback: CallbackQuery,
    state: FSMContext,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
) -> None:
    data = await state.get_data()
    topics: list[dict] = data.get("topics", [])
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные")
        await state.clear()
        return
    if not (0 <= idx < len(topics)):
        await callback.answer("Тема устарела — начни поиск заново /search")
        await state.clear()
        return
    await callback.answer()
    query = data.get("query", "")
    topic = topics[idx]

    status = await callback.message.answer("Анализирую тему…")
    try:
        if await tavily_exhausted(callback.message.bot, db):
            results = []
        else:
            results = await search.search(
                f"{query} {topic['title']}", settings.TAVILY_MAX_RESULTS
            )
            await db.add_tavily_spend(settings.TAVILY_CREDITS_PER_REQUEST)
        post = await _generate_post(llm, query, topic["title"], results)
    except (SearchError, LLMError) as exc:
        await _safe_delete(status)
        await _report_error(callback.message, exc)
        return
    await _safe_delete(status)
    texts = await _deliver_post(callback.message, post, results)
    await state.update_data(
        query=query,
        topic=topic["title"],
        sources=[
            {"title": r.title, "url": r.url, "content": r.content}
            for r in results
        ],
        post_texts=texts,
    )
    await state.set_state(PostActions.ready)
    if db_user is not None:
        await db.log_request(db_user["id"], "search", query)
        await db.save_post(
            db_user["id"], topic["title"],
            "\n\n---\n\n".join(post["posts"]),
            (post.get("media_suggestions") or [None])[0],
        )

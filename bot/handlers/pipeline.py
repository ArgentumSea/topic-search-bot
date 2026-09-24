import asyncio
import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services.query_expand import expand_query
from bot.config import settings
from bot.handlers.post_actions import PostActions
from bot.quota import tavily_exhausted
from bot.services.posting import _sources_relevant
from bot.db import Database
from bot.handlers.common import (
    SearchStates,
    _parse_topics,
    _report_error,
    _safe_delete,
)
from bot.services.llm import LLMError, LLMProvider, QuotaExhaustedError
from bot.services.posting import _deliver_post, _format_sources, _generate_post
from bot.services.prompts import load_prompt
from bot.services.search import SearchError, SearchProvider
from bot.utils import send_long

log = logging.getLogger(__name__)


async def _check_quota(
    message: Message, state: FSMContext, db: Database, db_user
) -> bool:
    if db_user is None:
        return True
    if not await db.check_and_consume_quota(
        db_user, settings.REQUESTS_LIMIT_DEFAULT
    ):
        await message.answer("Дневной лимит запросов иссяк. Попробуй завтра.")
        await state.clear()
        return False
    return True


async def run_search_pipeline(
    message: Message,
    state: FSMContext,
    query: str,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
) -> None:
    if not await _check_quota(message, state, db, db_user):
        return
    status = await message.answer("Ищу информацию…")
    query = await expand_query(llm, query)
    try:
        if await tavily_exhausted(message.bot, db):
            results = []
        else:
            results = await search.search(query, settings.TAVILY_MAX_RESULTS)
            await db.add_tavily_spend(settings.TAVILY_CREDITS_PER_REQUEST)
        src = (
            _format_sources(results)
            if results
            else "(поиск в сети недоступен — опирайся на свои знания)"
        )
        prompt = load_prompt("topics").format(query=query, sources=src)
        topics = _parse_topics(await llm.generate(prompt))
    except QuotaExhaustedError:
        await _safe_delete(status)
        await message.answer("Все модели временно недоступны: квота дня или перегрузка. Попробуйте через 10-15 минут.")
        await state.clear()
        return
    except (SearchError, LLMError) as exc:
        await _safe_delete(status)
        await _report_error(message, exc)
        await state.clear()
        return

    await _safe_delete(status)
    text = "Вот что актуально сейчас:\n\n" + "\n\n".join(
        f"{i}. {t['title']}\n{t['summary']}"
        for i, t in enumerate(topics, start=1)
    )
    # Telegram: максимум 8 кнопок в ряду — режем по 5
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=str(i), callback_data=f"topic:{i - 1}")
                for i in range(start, min(start + 5, len(topics) + 1))
            ]
            for start in range(1, len(topics) + 1, 5)
        ]
    )
    await send_long(message, text, keyboard)
    await state.update_data(
        topics=topics, query=query, source_urls=[r.url for r in results]
    )
    await state.set_state(SearchStates.choosing_topic)
    log.info(
        "topics shown",
        extra={"tg_id": message.from_user.id, "n": len(topics)},
    )


async def run_topic_pipeline(
    message: Message,
    state: FSMContext,
    query: str,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
) -> None:
    await state.clear()
    if not await _check_quota(message, state, db, db_user):
        return
    status = await message.answer("Анализирую тему…")
    query = await expand_query(llm, query)
    try:
        if await tavily_exhausted(message.bot, db):
            results = []
        else:
            results = await search.search(query, settings.TAVILY_MAX_RESULTS)
            await db.add_tavily_spend(settings.TAVILY_CREDITS_PER_REQUEST)
        _guard_ok = True
        if results:
            _guard_ok, post = await asyncio.gather(
                _sources_relevant(llm, query, results),
                _generate_post(llm, query, query, results),
            )
        else:
            post = await _generate_post(llm, query, query, results)
        if results and not _guard_ok:
            await _safe_delete(status)
            await message.answer(
                "По запросу не нашлось релевантных источников — пост"
                " получился бы некачественным. Попробуй переформулировать."
            )
            return
    except (SearchError, LLMError) as exc:
        await _safe_delete(status)
        await _report_error(message, exc)
        return

    await _safe_delete(status)
    texts = await _deliver_post(message, post, results)
    await state.update_data(
        query=query,
        topic=query,
        sources=[
            {"title": r.title, "url": r.url, "content": r.content}
            for r in results
        ],
        post_texts=texts,
    )
    await state.set_state(PostActions.ready)
    if db_user is not None:
        await db.log_request(db_user["id"], "topic", query)
        await db.save_post(
            db_user["id"], query,
            "\n\n---\n\n".join(post["posts"]),
            (post.get("media_suggestions") or [None])[0],
        )

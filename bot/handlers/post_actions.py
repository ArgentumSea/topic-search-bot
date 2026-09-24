"""Кнопки постобработки и история постов."""
import json
import asyncio
import re
import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.db import Database
from bot.handlers.common import _report_error, _safe_delete
from bot.services.llm import LLMError, LLMProvider
from bot.services.posting import (
    POST_SCHEMA,
    _compose_post_texts,
    _deliver_post,
    _format_sources,
    _parse_post,
)
from bot.services.prompts import load_prompt
from bot.services.search import SearchResult
from bot.utils import split_html

log = logging.getLogger(__name__)
router = Router()


class PostActions(StatesGroup):
    ready = State()


_MODS = {
    "regen": (
        "\n\nНапиши другой вариант того же поста: другие формулировки"
        " и подача, те же факты, структура и стиль."
    ),
    "short": (
        "\n\nПерепиши пост вдвое короче - не более 1500 символов."
        " Стиль, разметка, эмодзи-маркеры и финал-вопрос - как в образце."
    ),
    "noemoji": (
        "\n\nПерепиши тот же пост полностью без эмодзи: убери все"
        " эмодзи-маркеры, оставь жирные вводные фразы и структуру абзацев."
    ),
}


@router.message(Command("last"))
async def cmd_last(message: Message, db: Database, db_user) -> None:
    if db_user is None:
        return
    rows = await db.get_recent_posts(db_user["id"])
    if not rows:
        await message.answer("Твоих постов пока нет.")
        return
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{i}. {r['topic_text'][:40]}",
                callback_data=f"ppget:{r['id']}",
            )
        ]
        for i, r in enumerate(rows, 1)
    ]
    await message.answer(
        "Последние посты - нажми, чтобы получить текст:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("ppget:"))
async def get_post(callback: CallbackQuery, db: Database, db_user) -> None:
    post_id = int(callback.data.split(":")[1])
    row = await db.get_post_by_id(post_id, db_user["id"] if db_user else -1)
    if row is None:
        await callback.answer("Пост не найден")
        return
    await callback.answer()
    for part in split_html(row["post_text"]):
        await callback.message.answer(part, parse_mode="HTML")

_HEADER_RE = re.compile(r"^(<b>Пост \d+ из \d+</b>\n\n)")
_TAGLINE_RE = re.compile(r"\n\n(#[^\n]+)$", re.DOTALL)


def _split_post_text(text: str) -> tuple[str, str, str]:
    header = ""
    m = _HEADER_RE.match(text)
    if m:
        header = m.group(1)
        text = text[len(m.group(1)):]
    tags = ""
    m2 = _TAGLINE_RE.search(text)
    if m2:
        tags = m2.group(1).strip()
        text = text[: m2.start()]
    return header, text.strip(), tags



def _unwrap_maybe_json(text: str) -> str:
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            inner, _ = json.JSONDecoder().raw_decode(t)
        except (ValueError, json.JSONDecodeError):
            return text
        if isinstance(inner, dict):
            for v in inner.values():
                if isinstance(v, str) and v.strip():
                    return v
                if isinstance(v, list):
                    parts = [str(x) for x in v if str(x).strip()]
                    if parts:
                        return "\n\n".join(parts)
    return text


@router.callback_query(StateFilter(PostActions.ready, "PublishStates:waiting_media"), F.data.startswith("pp:"))
async def post_action(
    callback: CallbackQuery, state: FSMContext, llm: LLMProvider, db: Database, db_user
) -> None:
    parts = callback.data.split(":")
    action = parts[1]
    idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    data = await state.get_data()
    texts = data.get("post_texts") or []
    if not texts:
        await callback.answer("Нет сохранённого поста")
        return
    if idx >= len(texts):
        idx = 0
    text = texts[idx]

    if action == "publish":
        bound = await db.get_bound_channel()
        ch = None
        if bound is not None:
            ch = bound["chat_id"]
        else:
            from bot.config import settings

            if settings.CHANNEL_ID:
                raw = settings.CHANNEL_ID
                ch = int(raw) if raw.lstrip("-").isdigit() else raw
        if ch is None:
            await callback.answer("Канал не привязан (/channel)")
            return
        _h, body, tags = _split_post_text(text)
        pub = body + (("\n\n" + tags) if tags else "")
        await state.update_data(publish_ch=ch, media_items=[], pub_texts=[pub])
        await state.set_state(PublishStates.waiting_media)
        await callback.message.answer(
            "Пришли фото или видео для этого поста (можно несколько сообщений)."
            " Затем жми кнопку:",
            reply_markup=_publish_kb(),
        )
        await callback.answer()
        return


    _h, body, tags = _split_post_text(text)

    if action in ("short", "noemoji"):
        instruction = _MODS[action].strip()
        status = await callback.message.answer("Пишу вариант…")
        try:
            new_raw = (
                await llm.generate(instruction + "\n\nТекст поста:\n" + body)
            ).strip()
        except LLMError as exc:
            await _safe_delete(status)
            await _report_error(callback.message, exc)
            return
        await _safe_delete(status)
        new_raw = new_raw.removeprefix("```html").removeprefix("```").removesuffix("```").strip()
        new_raw = _unwrap_maybe_json(new_raw).replace(chr(92) + "n", chr(10)).replace(chr(13) + chr(10), chr(10))
        _, nb, nt = _split_post_text(new_raw)
        tags_final = tags or nt
        texts[idx] = _h + nb + (("\n\n" + tags_final) if tags_final else "")
        await state.update_data(post_texts=texts)
        from bot.services.posting import post_actions_keyboard as _pakb
        _last = None
        for part in split_html(texts[idx]):
            _last = await callback.message.answer(part, parse_mode="HTML")
        if _last is not None:
            await _last.edit_reply_markup(reply_markup=_pakb(idx))
        await callback.answer()
        return

    if action == "regen":
        query = data.get("query", "")
        topic = data.get("topic", query)
        sources = [SearchResult(**d) for d in data.get("sources", [])]
        status = await callback.message.answer("Пишу вариант…")
        src = (
            _format_sources(sources)
            if sources
            else "(поиск в сети недоступен - опирайся на свои знания)"
        )
        prompt = (
            load_prompt("post").format(query=query, topic=topic, sources=src)
            + _MODS["regen"]
        )
        try:
            post = _parse_post(await llm.generate(prompt, schema=POST_SCHEMA))
        except LLMError as exc:
            await _safe_delete(status)
            await _report_error(callback.message, exc)
            return
        await _safe_delete(status)
        texts = await _deliver_post(callback.message, post, sources)
        await state.update_data(post_texts=texts)
        await callback.answer()
        return

    await callback.answer("Неизвестное действие")


def _publish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="С медиа", callback_data="ppub:done"),
                InlineKeyboardButton(text="Без медиа", callback_data="ppub:skip"),
                InlineKeyboardButton(text="Отмена", callback_data="ppub:cancel"),
            ]
        ]
    )


class PublishStates(StatesGroup):
    waiting_media = State()


_media_locks: dict[int, asyncio.Lock] = {}

@router.message(PublishStates.waiting_media, F.photo | F.video)
async def collect_media(message: Message, state: FSMContext) -> None:
    _lock = _media_locks.setdefault(message.chat.id, asyncio.Lock())
    async with _lock:
        if message.photo:
            item = {"id": message.photo[-1].file_id, "kind": "photo"}
        else:
            item = {"id": message.video.file_id, "kind": "video"}
        data = await state.get_data()
        items = data.get("media_items") or []
        items.append(item)
        old_status = data.get("media_status_id")
        if old_status:
            try:
                await message.bot.delete_message(message.chat.id, old_status)
            except Exception:  # noqa: BLE001
                pass
        status_msg = await message.answer(
            f"Медиа добавлено: {len(items)}. Можно ещё, затем:",
            reply_markup=_publish_kb(),
        )
        await state.update_data(
            media_items=items, media_status_id=status_msg.message_id
        )


@router.message(PublishStates.waiting_media, Command("skip"))
async def publish_skip(message: Message, state: FSMContext) -> None:
    await _do_publish(message, state, with_media=False)


@router.message(PublishStates.waiting_media, Command("done"))
async def publish_done(message: Message, state: FSMContext) -> None:
    await _do_publish(message, state, with_media=True)


async def _do_publish(
    message: Message,
    state: FSMContext,
    with_media: bool,
    caption_texts: list | None = None,
) -> None:
    from aiogram.types import InputMediaPhoto, InputMediaVideo

    data = await state.get_data()
    ch = data.get("publish_ch")
    texts = caption_texts or data.get("pub_texts") or data.get("post_texts") or []
    items = data.get("media_items") or []
    status_id = data.get("media_status_id")
    if status_id:
        try:
            await message.bot.delete_message(message.chat.id, status_id)
        except Exception:  # noqa: BLE001
            pass
    await state.set_state(PostActions.ready)
    if ch is None:
        await message.answer("Сессия потеряна - сгенерируй пост заново.")
        return
    try:
        if with_media and items:
            clean = []
            for _t in texts:
                _h, _b, _tg = _split_post_text(_t)
                clean.append(_b + (("\n\n" + _tg) if _tg else ""))
            caption = "\n\n".join(x for x in clean if x)
            if len(caption) > 1024:
                caption = caption[:1020]
                cut = caption.rfind("\n")
                if cut > 500:
                    caption = caption[:cut]
                caption += "…"
            group = []
            for idx, it in enumerate(items):
                cls = (
                    InputMediaVideo
                    if it["kind"] == "video"
                    else InputMediaPhoto
                )
                kwargs: dict = {"media": it["id"]}
                if idx == 0:
                    kwargs["caption"] = caption
                    kwargs["parse_mode"] = "HTML"
                group.append(cls(**kwargs))
            await message.bot.send_media_group(ch, group)
        else:
            for t in texts:
                for part in split_html(t):
                    await message.bot.send_message(ch, part, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        await message.answer(
            f"Не удалось опубликовать: {exc}\n"
            "Проверь, что бот - администратор канала с правом публикации."
        )
        return
    await message.answer("Опубликовано в канал.")


@router.callback_query(PublishStates.waiting_media, F.data == "ppub:done")
async def publish_done_cb(
    callback: CallbackQuery, state: FSMContext, llm: LLMProvider
) -> None:
    data = await state.get_data()
    if not data.get("media_items"):
        await callback.message.answer(
            "Сначала пришли фото или видео для поста, затем жми «С медиа»."
            " Или «Без медиа», чтобы опубликовать только текст."
        )
        await callback.answer()
        return
    await callback.answer()
    query = data.get("query", "")
    topic = data.get("topic", query)
    sources = [SearchResult(**d) for d in data.get("sources", [])]
    src = (
        _format_sources(sources)
        if sources
        else "(поиск в сети недоступен - опирайся на свои знания)"
    )
    status = await callback.message.answer("Сокращаю пост под медиа…")
    prompt = (
        load_prompt("post").format(query=query, topic=topic, sources=src)
        + "\n\nСократи этот пост примерно на 400 символов: сохрани заголовок,"
        " все пункты, разметку, стиль и финал. Общий объем - не более 950"
        " символов, чтобы текст поместился в подпись к медиа."
    )
    try:
        short = _parse_post(await llm.generate(prompt, schema=POST_SCHEMA))
    except LLMError as exc:
        await _safe_delete(status)
        await _report_error(callback.message, exc)
        return
    await _safe_delete(status)
    short_texts = _compose_post_texts(short)
    await state.update_data(pub_texts=short_texts)
    await _do_publish(
        callback.message, state, with_media=True, caption_texts=short_texts
    )


@router.callback_query(PublishStates.waiting_media, F.data == "ppub:skip")
async def publish_skip_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _do_publish(callback.message, state, with_media=False)


@router.callback_query(PublishStates.waiting_media, F.data == "ppub:cancel")
async def publish_cancel_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostActions.ready)
    await callback.answer("Отменено")
    await callback.message.answer("Публикация отменена.")


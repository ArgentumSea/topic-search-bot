import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db import Database
from bot.handlers.common import SearchStates
from bot.handlers.pipeline import run_search_pipeline, run_topic_pipeline
from bot.handlers.posts import TopicStates
from bot.services.llm import LLMProvider
from bot.services.prompts import load_prompt
from bot.services.search import SearchProvider
from bot.services.voice import VoiceTranscriber

log = logging.getLogger(__name__)
router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # лимит Bot API на скачивание


async def _download(bot, file_id: str) -> Path:
    buffer = await bot.download(file_id)
    buffer.seek(0)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    tmp.write(buffer.read())
    tmp.close()
    return Path(tmp.name)


def _file_of(message: Message):
    """Возвращает (файловый объект, mime) или None."""
    if message.voice:
        return message.voice, "audio/ogg"
    if message.audio:
        return message.audio, message.audio.mime_type or "audio/mpeg"
    if message.photo:
        return message.photo[-1], "image/jpeg"
    if message.video:
        return message.video, "video/mp4"
    if message.video_note:
        return message.video_note, "video/mp4"
    if message.document:
        return message.document, (
            message.document.mime_type or "application/octet-stream"
        )
    return None


async def _extract_query(
    message: Message, llm: LLMProvider, transcriber: VoiceTranscriber
) -> str | None:
    """Файл -> формулировка запроса (Vosk для голоса, Gemini для остального)."""
    file_obj, mime = _file_of(message)
    if file_obj is None:
        return None
    if getattr(file_obj, "file_size", 0) and file_obj.file_size > MAX_FILE_SIZE:
        await message.answer("Файл больше 20 МБ — не смогу обработать.")
        return None

    path = await _download(message.bot, file_obj.file_id)
    try:
        if mime.startswith("audio/"):
            query = await transcriber.transcribe(str(path))
        else:
            query = await llm.generate_with_file(
                load_prompt("file"), str(path), mime
            )
        query = (query or "").strip()
        if not query:
            return None
        log.info(
            "file query extracted",
            extra={"mime": mime, "query": query[:80]},
        )
        return query
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "file processing failed", extra={"mime": mime, "error": str(exc)}
        )
        return None
    finally:
        path.unlink(missing_ok=True)


@router.message(
    (
        F.voice | F.audio | F.photo | F.video
        | F.video_note | F.document
    ),
    StateFilter(None, SearchStates.waiting_query),
)
async def file_as_search(
    message: Message,
    state: FSMContext,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
    transcriber: VoiceTranscriber,
) -> None:
    query = await _extract_query(message, llm, transcriber)
    if not query and message.caption:
        query = message.caption.strip()
    if not query:
        await message.answer(
            "Не смог понять запрос из файла. Попробуй текстом или другой файл."
        )
        return
    await run_search_pipeline(message, state, query, search, llm, db, db_user)


@router.message(
    (
        F.voice | F.audio | F.photo | F.video
        | F.video_note | F.document
    ),
    TopicStates.waiting_topic,
)
async def file_as_topic(
    message: Message,
    state: FSMContext,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
    transcriber: VoiceTranscriber,
) -> None:
    query = await _extract_query(message, llm, transcriber)
    if not query and message.caption:
        query = message.caption.strip()
    if not query:
        await message.answer(
            "Не смог понять тему из файла. Попробуй текстом или другой файл."
        )
        return
    await run_topic_pipeline(message, state, query, search, llm, db, db_user)

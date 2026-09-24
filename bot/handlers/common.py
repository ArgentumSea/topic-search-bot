import json
import logging

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.services.llm import LLMError, QuotaExhaustedError

log = logging.getLogger(__name__)


class SearchStates(StatesGroup):
    waiting_query = State()
    choosing_topic = State()


def _parse_topics(raw: str) -> list[dict]:
    try:
        # raw_decode: берём ровно первый JSON-массив, хвост игнорируем
        # (модель любит фенсы ```json и пояснения после ответа)
        data, _ = json.JSONDecoder().raw_decode(raw[raw.index("[") :])
    except (ValueError, json.JSONDecodeError) as exc:
        raise LLMError("модель вернула некорректный формат") from exc
    topics = [
        {"title": str(t.get("title", "")), "summary": str(t.get("summary", ""))}
        for t in data
        if isinstance(t, dict) and t.get("title")
    ]
    if not topics:
        raise LLMError("темы не найдены")
    return topics


async def _safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        log.warning("cannot delete status message", extra={"error": str(exc)})


async def _report_error(message: Message, exc: Exception) -> None:
    if isinstance(exc, QuotaExhaustedError):
        await message.answer("Все модели временно недоступны: квота дня или перегрузка. Попробуйте через 10-15 минут.")
        try:
            from bot.config import settings

            for admin_id in settings.ADMIN_IDS:
                await message.bot.send_message(
                    admin_id,
                    "⚠️ Gemini: лимиты всех моделей иссякли. Бот отвечает"
                    " пользователям заглушкой, пока квота не восстановится.",
                )
        except Exception:  # noqa: BLE001
            pass
    else:
        await message.answer(f"Ошибка обработки: {exc}")
    log.warning("processing failed", extra={"error": str(exc)})

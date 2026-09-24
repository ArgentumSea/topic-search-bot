"""Учёт исчерпания месячной квоты Tavily и уведомления пользователей."""
import asyncio
import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot.config import settings
from bot.db import Database

log = logging.getLogger(__name__)


async def tavily_exhausted(bot: Bot, db: Database) -> bool:
    """True - квота иссякла. При смене состояния уведомляет всех."""
    spent, reset = await db.get_tavily_usage()
    exhausted = spent >= settings.TAVILY_MONTHLY_BUDGET
    flag = (await db._kv_get("tavily_exhausted")) == "1"
    if exhausted != flag:
        await db._kv_set("tavily_exhausted", "1" if exhausted else "0")
        await _notify(bot, db, exhausted, reset)
        log.info("quota state changed", extra={"exhausted": exhausted})
    return exhausted


async def _notify(bot: Bot, db: Database, exhausted: bool, reset: date) -> None:
    if exhausted:
        days = (reset - date.today()).days
        text = (
            "Квота запросов иссякла, далее возможен подбор без актуализации"
            " новостей через Gemini\n"
            f"Обновление через {days} дней ({reset.strftime('%d.%m.%Y')})"
        )
    else:
        text = (
            "Ежемесячная квота обновлена, актуальный поиск информации"
            " работает в полном объеме"
        )
    for tg_id in await db.list_active_tg_ids():
        try:
            await bot.send_message(tg_id, text)
            await asyncio.sleep(0.4)  # throttle: не дёргаем flood control
        except TelegramRetryAfter as ra:
            await asyncio.sleep(ra.retry_after + 1)
            try:
                await bot.send_message(tg_id, text)
            except Exception as exc:  # noqa: BLE001
                log.warning("quota notify retry failed", extra={"tg_id": tg_id})
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "quota notify failed", extra={"tg_id": tg_id, "error": str(exc)[:80]}
            )

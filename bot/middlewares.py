import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from bot.db import Database

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """Пропускает только зарегистрированных и незаблокированных."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            db_user = await self.db.get_user_by_tg_id(user.id)
            data["db_user"] = db_user
            if db_user is not None and db_user["is_blocked"]:
                if isinstance(event, (Message, CallbackQuery)):
                    await event.answer("Вы заблокированы.")
                log.info("blocked user attempt", extra={"tg_id": user.id})
                return
        return await handler(event, data)

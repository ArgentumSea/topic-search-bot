"""Привязка каналов: /channel + автодетект через my_chat_member."""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.db import Database

log = logging.getLogger(__name__)
router = Router()


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, db: Database) -> None:
    """Авторегистрация каналов, где бот стал админом."""
    if event.chat.type != "channel":
        return
    status = event.new_chat_member.status
    if status == "administrator":
        await db.upsert_channel(event.chat.id, event.chat.title)
        log.info("channel registered", extra={"chat_id": event.chat.id})
    elif status in ("left", "kicked"):
        await db.set_channel_inactive(event.chat.id)
        log.info("channel deactivated", extra={"chat_id": event.chat.id})


def _channel_card(row) -> tuple[str, InlineKeyboardMarkup]:
    title = row["title"] or str(row["chat_id"])
    bound = bool(row["bound"])
    text = f"{'Привязан: ' if bound else ''}Канал: {title}"
    btn = InlineKeyboardButton(
        text="Отвязать" if bound else "Привязать",
        callback_data=f"ch:{row['chat_id']}:{'unbind' if bound else 'bind'}",
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.message(Command("channel"))
async def cmd_channel(message: Message, db: Database, db_user) -> None:
    if db_user is None or db_user["role"] not in ("admin", "assistant"):
        await message.answer("Нет доступа к этой команде.")
        return
    rows = await db.list_channels()
    if not rows:
        await message.answer(
            "Каналов нет. Добавь бота администратором в канал - "
            "он появится в списке автоматически."
        )
        return
    for r in rows:
        text, keyboard = _channel_card(r)
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ch:"))
async def channel_action(callback: CallbackQuery, db: Database, db_user) -> None:
    if db_user is None or db_user["role"] not in ("admin", "assistant"):
        await callback.answer("отказ - нет прав")
        return
    _, chat_id, action = callback.data.split(":")
    if action == "bind":
        await db.bind_channel(int(chat_id))
        log.info("channel bound", extra={"chat_id": chat_id})
    elif action == "unbind":
        await db.unbind_channels()
        log.info("channel unbound", extra={"chat_id": chat_id})
    else:
        await callback.answer("Неизвестное действие")
        return
    rows = await db.list_channels()
    row = next((r for r in rows if r["chat_id"] == int(chat_id)), None)
    await callback.answer("Готово")
    if row is None:
        await callback.message.delete()
        return
    text, keyboard = _channel_card(row)
    await callback.message.edit_text(text, reply_markup=keyboard)

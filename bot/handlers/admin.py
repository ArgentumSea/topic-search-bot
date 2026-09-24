import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from datetime import date

from bot.config import settings
from bot.db import Database

log = logging.getLogger(__name__)
router = Router()

ROLE_LABEL = {
    "admin": "Администратор",
    "assistant": "Ассистент",
    "user": "Пользователь",
}


def _can_manage(actor_role: str, target_role: str) -> bool:
    """Админ управляет ассистентами и пользователями (не админами).
    Ассистент — только пользователями."""
    if actor_role == "admin":
        return target_role != "admin"
    if actor_role == "assistant":
        return target_role == "user"
    return False


def _display_name(row) -> str:
    return f"@{row['username']}" if row["username"] else str(row["tg_id"])


def _user_card(row) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка пользователя: текст + его личные кнопки действий."""
    tg = row["tg_id"]
    text = (
        f"{_display_name(row)} — {ROLE_LABEL[row['role']]}"
        f" — {row['created_at'][:10]}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔽 разжаловать" if row["role"] == "assistant" else "🔼 ассистент",
                    callback_data=f"adm:{tg}:toggle_role",
                ),
                InlineKeyboardButton(
                    text="🔓 разблокировать" if row["is_blocked"] else "🔒 заблокировать",
                    callback_data=f"adm:{tg}:toggle_block",
                ),
                InlineKeyboardButton(text="🗑 удалить", callback_data=f"adm:{tg}:delete"),
            ]
        ]
    )
    return text, keyboard


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database, db_user) -> None:
    if db_user is None or db_user["role"] not in ("admin", "assistant"):
        await message.answer("Нет доступа к этой команде.")
        return
    rows = await db.list_users()
    lines = [
        f"{_display_name(r)} — {ROLE_LABEL[r['role']]}" for r in rows
    ]
    await message.answer(
        "Пользователи:\n" + ("\n".join(lines) if lines else "пусто")
    )
    for r in rows:
        if _can_manage(db_user["role"], r["role"]):
            text, keyboard = _user_card(r)
            await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm:"))
async def admin_action(
    callback: CallbackQuery, db: Database, db_user
) -> None:
    _, tg_id_str, action = callback.data.split(":")
    target = await db.get_user_by_tg_id(int(tg_id_str))
    if target is None or target["is_deleted"]:
        await callback.answer("Пользователь не найден")
        return
    if not _can_manage(db_user["role"], target["role"]):
        await callback.answer("отказ - нет прав")
        return

    if action == "toggle_role":
        new_role = "assistant" if target["role"] == "user" else "user"
        await db.set_role(target["id"], new_role)
        log.info("role changed", extra={"tg": tg_id_str, "role": new_role})
    elif action == "toggle_block":
        await db.set_blocked(target["id"], not target["is_blocked"])
        log.info("block toggled", extra={"tg": tg_id_str})
    elif action == "delete":
        if not target["is_blocked"]:
            await callback.answer("Сначала заблокируй пользователя")
            return
        await db.soft_delete(target["id"])
        log.info("user deleted", extra={"tg": tg_id_str})
        await callback.answer("Удалён")
        await callback.message.delete()
        return
    else:
        await callback.answer("Неизвестное действие")
        return

    await callback.answer("Готово")
    target = await db.get_user_by_tg_id(int(tg_id_str))
    if target is not None and not target["is_deleted"]:
        text, keyboard = _user_card(target)
        if _can_manage(db_user["role"], target["role"]):
            await callback.message.edit_text(text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(text)


@router.message(Command("stat"))
async def cmd_stat(message: Message, db: Database, db_user) -> None:
    if db_user is None or db_user["role"] not in ("admin", "assistant"):
        await message.answer("Нет доступа к этой команде.")
        return
    rows = await db.get_stats()
    if not rows:
        await message.answer("Пока нет данных.")
        return
    lines = [
        f"{_display_name(r)} — {r['requests_today']} — {r['requests_total']}"
        for r in rows
    ]
    spent, reset = await db.get_tavily_usage()
    days_left = (reset - date.today()).days
    header = (f"Tavily: потрачено {spent} из {settings.TAVILY_MONTHLY_BUDGET}\n"
              f"До обновления {days_left} дней ({reset.strftime('%d.%m.%Y')})\n\n")
    await message.answer(
        header + "user — запросов за сутки — за всё время\n\n" + "\n".join(lines)
    )

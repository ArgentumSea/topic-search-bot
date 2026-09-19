import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.config import settings
from bot.db import Database

log = logging.getLogger(__name__)
router = Router()

HELP_TEXT = (
    "Я бот для поиска актуальной информации в сети "
    "и создания постов для твоего блога в Telegram.\n\n"
    "Отправь мне описание темы или файл (фото, видео, аудио, документ) — "
    "я проанализирую и сделаю подборку тем. Когда выберешь тему, "
    "я напишу для тебя пост.\n"
    "Если у тебя уже есть готовая тема, используй /topic.\n\n"
    "Команды:\n"
    "/search — подборка актуальных тем по запросу\n"
    "/topic — пост по готовой теме\n"
    "/cancel — прервать текущее действие\n"
    "/help — эта справка"
)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, bot: Bot) -> None:
    user, created = await db.register_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
    )
    if user["is_blocked"]:
        await message.answer("Вы заблокированы.")
        return
    if created:
        log.info("new user", extra={"tg_id": message.from_user.id})
        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"Новый пользователь — "
                    f"{message.from_user.username or message.from_user.id}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "failed to notify admin",
                    extra={"admin_id": admin_id, "error": str(exc)},
                )
    await message.answer(
        "Привет! Я бот для поиска актуальной информации в сети "
        "и создания постов для твоего блога в Telegram.\n"
        "Отправь мне описание темы или файл (фото, видео, аудио, документ).\n"
        "Я проанализирую и сделаю подборку тем, когда ты выберешь тему, "
        "я напишу для тебя пост.\n"
        "Если у тебя уже есть готовая тема, напиши мне — я изучу информацию "
        "и подготовлю пост.\n\n"
        "Подробности: /help"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)

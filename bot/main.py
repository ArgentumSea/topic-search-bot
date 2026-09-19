import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

from bot.config import settings
from bot.db import Database
from bot.handlers import admin as admin_handlers
from bot.handlers import files as files_handlers
from bot.handlers import posts as posts_handlers
from bot.handlers import search as search_handlers
from bot.handlers import start as start_handlers
from bot.logging_setup import setup_logging
from bot.middlewares import AccessMiddleware
from bot.services.llm import GeminiProvider
from bot.services.search import TavilyProvider
from bot.services.voice import VoiceTranscriber

log = logging.getLogger(__name__)


async def healthcheck(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def main() -> None:
    setup_logging(settings.LOG_LEVEL)
    db = Database(settings.DATABASE_PATH)
    await db.init()
    await db.sync_admins(settings.ADMIN_IDS)
    log.info("database ready", extra={"path": settings.DATABASE_PATH})

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["search"] = TavilyProvider(
        api_key=settings.TAVILY_API_KEY,
        search_depth=settings.TAVILY_SEARCH_DEPTH,
    )
    dp["llm"] = GeminiProvider(
        api_key=settings.GEMINI_API_KEY, models=settings.LLM_MODELS
    )
    dp["transcriber"] = VoiceTranscriber(settings.VOSK_MODEL_PATH)

    dp.include_router(start_handlers.router)
    dp.include_router(search_handlers.router)
    dp.include_router(posts_handlers.router)
    dp.include_router(files_handlers.router)
    dp.include_router(admin_handlers.router)
    dp.message.middleware(AccessMiddleware(db))
    dp.callback_query.middleware(AccessMiddleware(db))

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="search", description="Подборка тем по запросу"),
            BotCommand(command="topic", description="Пост по готовой теме"),
            BotCommand(command="cancel", description="Прервать действие"),
        ]
    )

    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)

    if settings.USE_WEBHOOK:
        webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_handler.register(app, path="/webhook")
        setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    port = (
        settings.WEBHOOK_PORT if settings.USE_WEBHOOK
        else settings.HEALTHCHECK_PORT
    )
    await web.TCPSite(runner, "0.0.0.0", port).start()

    try:
        if settings.USE_WEBHOOK:
            log.info("starting webhook", extra={"url": settings.WEBHOOK_URL})
            await bot.set_webhook(
                settings.WEBHOOK_URL, drop_pending_updates=True
            )
            await asyncio.Event().wait()
        else:
            log.info("starting polling")
            await dp.start_polling(bot)
    finally:
        log.info("shutting down")
        if settings.USE_WEBHOOK:
            await bot.delete_webhook()
        await runner.cleanup()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db import Database
from bot.handlers.common import SearchStates
from bot.handlers.pipeline import run_search_pipeline
from bot.services.llm import LLMProvider
from bot.services.search import SearchProvider

log = logging.getLogger(__name__)
router = Router()


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.")


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(since=message.date.timestamp())
    await message.answer("На какую тему искать информацию?")


@router.message(
    SearchStates.waiting_query, F.text.func(lambda t: bool(t) and not t.startswith("/"))
)
async def process_query(
    message: Message,
    state: FSMContext,
    search: SearchProvider,
    llm: LLMProvider,
    db: Database,
    db_user,
) -> None:
    _guard = (await state.get_data()).get("since")
    if _guard and message.date.timestamp() < _guard:
        return
    query = message.text.strip()
    await run_search_pipeline(message, state, query, search, llm, db, db_user)



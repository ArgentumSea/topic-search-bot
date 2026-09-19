import logging
import re

from aiogram.types import InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096

_TAG_RE = re.compile(r"<(/?)(b|i|u|s|code|a)(?:\s+href=\"[^\"]*\")?>")


def _tag_stack(text: str) -> list[tuple[str, str]]:
    """Возвращает стек открытых тегов [(name, open_tag), ...]."""
    stack: list[tuple[str, str]] = []
    for m in _TAG_RE.finditer(text):
        closing, name = m.group(1), m.group(2)
        if closing:
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][0] == name:
                    del stack[j]
                    break
        else:
            stack.append((name, m.group(0)))
    return stack


def _close(stack: list[tuple[str, str]]) -> str:
    return "".join(f"</{name}>" for name, _ in reversed(stack))


def _reopen(stack: list[tuple[str, str]]) -> str:
    return "".join(open_tag for _, open_tag in stack)


def split_html(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Режет HTML-текст на части <= limit, сохраняя баланс тегов."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    stack: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) + len(_close(stack)) <= limit:
            current = candidate
            stack = _tag_stack(current)
            continue
        if current:
            parts.append(current + _close(stack))
        if len(block) + 20 > limit:
            log.warning("oversized paragraph, hard split")
            rest = block
            while len(rest) + 20 > limit:
                chunk = rest[:limit]
                st = _tag_stack(chunk)
                parts.append(chunk + _close(st))
                rest = _reopen(st) + rest[limit:]
            current, stack = rest, _tag_stack(rest)
        else:
            current, stack = block, _tag_stack(block)
    if current:
        parts.append(current + _close(stack))
    return parts


def split_text(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Режет plain-текст на части по границам строк."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n"):
        if len(block) > limit:
            if current:
                parts.append(current)
                current = ""
            for i in range(0, len(block), limit):
                parts.append(block[i : i + limit])
        elif len(current) + len(block) + 1 > limit:
            parts.append(current)
            current = block
        else:
            current = f"{current}\n{block}" if current else block
    if current:
        parts.append(current)
    return parts


async def send_long(
    message: Message,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """Отправляет длинный текст частями; клавиатуру крепит к последней."""
    parts = split_html(text) if parse_mode == "HTML" else split_text(text)
    for i, part in enumerate(parts):
        if i == len(parts) - 1 and keyboard is not None:
            await message.answer(
                part, reply_markup=keyboard, parse_mode=parse_mode
            )
        else:
            await message.answer(part, parse_mode=parse_mode)

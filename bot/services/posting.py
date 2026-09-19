import json
import logging

from aiogram.types import Message

from bot.services.llm import LLMError, LLMProvider
from bot.services.prompts import load_prompt
from bot.utils import split_html

log = logging.getLogger(__name__)


def _format_sources(results) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        excerpt = r.content[:500].replace("\n", " ")
        lines.append(f"[{i}] {r.title}\nURL: {r.url}\n{excerpt}")
    return "\n\n".join(lines)


def _parse_post(raw: str) -> dict:
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[raw.index("{") :])
    except (ValueError, json.JSONDecodeError) as exc:
        raise LLMError("модель вернула некорректный формат") from exc
    posts = [str(p) for p in data.get("posts", []) if str(p).strip()]
    if not posts:
        raise LLMError("пост не получен")
    return {
        "posts": posts,
        "hashtags": [str(h) for h in data.get("hashtags", [])],
        "media_suggestion": str(data.get("media_suggestion", "")).strip(),
    }


async def _generate_post(
    llm: LLMProvider, query: str, topic: str, results
) -> dict:
    prompt = load_prompt("post").format(
        query=query, topic=topic, sources=_format_sources(results)
    )
    return _parse_post(await llm.generate(prompt))


def _compose_post_texts(post: dict, source_urls: list[str]) -> list[str]:
    """Хештеги в каждый пост, источники — в последний."""
    hashtags = " ".join(
        f"#{h.lstrip('#').replace(' ', '_')}" for h in post["hashtags"]
    )
    n = len(post["posts"])
    texts = []
    for i, body in enumerate(post["posts"]):
        header = f"<b>Пост {i + 1} из {n}</b>\n\n" if n > 1 else ""
        text = f"{header}{body}"
        if hashtags:
            text += f"\n\n{hashtags}"
        if i == n - 1 and source_urls:
            text += "\n\n<b>Источники:</b>\n" + "\n".join(source_urls)
        texts.append(text)
    return texts


async def _deliver_post(
    message: Message, post: dict, source_urls: list[str]
) -> None:
    from html import unescape

    from bot.utils import split_text

    for text in _compose_post_texts(post, source_urls):
        for part in split_html(text):
            try:
                await message.answer(part, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001 — битый HTML от модели
                log.warning(
                    "HTML send failed, fallback to plain",
                    extra={"error": str(exc)},
                )
                for plain in split_text(unescape(re.sub(r"<[^>]+>", "", part))):
                    await message.answer(plain)
    if post["media_suggestion"]:
        await message.answer(
            f"<b>Медиаконтент:</b>\n{post['media_suggestion']}",
            parse_mode="HTML",
        )

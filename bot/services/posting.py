import random
import re
import re
import json
import logging

from bot.config import settings

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.services.llm import LLMError, LLMProvider
from bot.services.prompts import load_prompt
from bot.utils import split_html

log = logging.getLogger(__name__)

POST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "posts": {"type": "ARRAY", "items": {"type": "STRING"}},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "media_suggestions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["posts", "hashtags", "media_suggestions"],
}


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
        log.warning("parse raw", extra={"raw": raw[:1500]})
        raise LLMError("модель вернула некорректный формат") from exc
    posts = [str(p) for p in data.get("posts", []) if str(p).strip()]
    unwrapped = []
    for _p in posts:
        _t = _p.strip()
        if _t.startswith('{') and "post" in _t:
            try:
                _inner, _ = json.JSONDecoder().raw_decode(_t)
            except (ValueError, json.JSONDecodeError):
                _inner = None
            if isinstance(_inner, dict):
                if _inner.get("post"):
                    unwrapped.append(str(_inner["post"]))
                    continue
                if _inner.get("posts"):
                    unwrapped.extend(str(x) for x in _inner["posts"])
                    continue
        unwrapped.append(_p)
    posts = [x for x in unwrapped if x.strip()]
    posts = [x.replace(chr(92) + "n", chr(10)).replace(chr(13) + chr(10), chr(10)).strip() for x in posts]
    if not posts:
        raise LLMError("пост не получен")
    medias = [str(m).strip() for m in (data.get("media_suggestions") or [])]
    if not medias:
        one = str(data.get("media_suggestion", "")).strip()
        medias = [one] * len(posts) if one else []
    return {
        "posts": posts,
        "hashtags": [str(h) for h in data.get("hashtags", [])],
        "media_suggestions": medias,
    }


_STRUCTURE_VARIANTS = [
    "Формат подачи: тезисы с эмодзи-маркерами и жирными вводными, 2-4 пункта.",
    "Формат подачи: сплошной текст с абзацами, 1-2 эмодзи-акцента в ключевых местах, без списков.",
    "Формат подачи: короткий плотный пост без списков, с эмодзи-акцентом в начале и в финале.",
    "Формат подачи: разбор по шагам с эмодзи-нумерацией этапов.",
    "Формат подачи: вопрос-ответ или дилемма, с эмодзи-акцентами по тексту."
]

async def _generate_post(
    llm: LLMProvider, query: str, topic: str, results
) -> dict:
    src = (
        _format_sources(results)
        if results
        else "(поиск в сети недоступен — опирайся на свои знания)"
    )
    prompt = load_prompt("post").format(
        query=query, topic=topic, sources=src
    )
    prompt = prompt.replace("Верни строго JSON", random.choice(_STRUCTURE_VARIANTS) + "\n\n" + "Верни строго JSON", 1)
    return _parse_post(await llm.generate(prompt, schema=POST_SCHEMA))


def _compose_post_texts(post: dict) -> list[str]:
    """Хештеги в каждый пост, источники — в последний."""
    hashtags = " ".join(
        f"#{h.lstrip('#').replace(' ', '_')}" for h in post["hashtags"]
    )
    posts = list(post["posts"])
    while len(posts) > 1 and len(posts[-1]) < 300:
        posts[-2] = posts[-2].rstrip() + "\n\n" + posts[-1].lstrip()
        posts.pop()
    n = len(posts)
    texts = []
    for i, body in enumerate(posts):
        body = "\n".join(
            ln for ln in body.split("\n") if not ln.strip().startswith("#")
        ).strip()
        header = f"<b>Пост {i + 1} из {n}</b>\n\n" if n > 1 else ""
        text = f"{header}{body}"
        if hashtags:
            text += f"\n\n{hashtags}"
        texts.append(text)
    return texts




def post_actions_keyboard(index: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Без эмодзи", callback_data=f"pp:noemoji:{index}"),
                InlineKeyboardButton(text="Короче", callback_data=f"pp:short:{index}"),
            ],
            [InlineKeyboardButton(text="Ещё вариант", callback_data=f"pp:regen:{index}")],
            [
                InlineKeyboardButton(text="Опубликовать", callback_data=f"pp:publish:{index}"),
            ],
        ]
    )


def _post_medias(post: dict) -> list[str]:
    """Медиапромпты, выровненные по списку posts."""
    posts_n = len(post.get("posts") or [])
    medias = [str(m).strip() for m in (post.get("media_suggestions") or [])]
    if not medias:
        one = str(post.get("media_suggestion") or "").strip()
        medias = [one] * posts_n if one else []
    if len(medias) < posts_n:
        medias += [""] * (posts_n - len(medias))
    return medias[:posts_n]


async def _deliver_post(
    message: Message, post: dict, source_urls: list
) -> list[str]:
    """Источники, затем каждый пост со своим медиапромптом и клавиатурой."""
    texts = _compose_post_texts(post)
    medias = _post_medias(post)
    raw = list(post["posts"])
    while len(raw) > 1 and len(raw[-1]) < 300:
        raw.pop()
        if medias:
            medias.pop()
    medias = (medias + [""] * len(texts))[: len(texts)]
    last = None
    if source_urls:
        src_block = "\n\n".join(
            f"{j}. {r.title}\n{r.url}" for j, r in enumerate(source_urls, 1)
        )
        last = await message.answer(
            "<b>Источники:</b>\n" + src_block, parse_mode="HTML"
        )
    for i, text in enumerate(texts):
        for part in split_html(text):
            last = await message.answer(part, parse_mode="HTML")
        if i < len(medias) and medias[i]:
            last = await message.answer(medias[i], parse_mode="HTML")
        if last is not None:
            await last.edit_reply_markup(reply_markup=post_actions_keyboard(i))
    return texts


async def _sources_relevant(llm, query: str, results) -> bool:
    """Джемини одним вызовом решает, по теме ли источники."""
    titles = "\n".join(f"- {r.title}" for r in results[:15])
    prompt = (
        f"Запрос пользователя: {query}\nНайденные источники:\n{titles}\n\n"
        "Ответь строго одним словом YES или NO: относятся ли источники к теме запроса?"
    )
    try:
        ans = (await llm.generate(prompt)).strip().upper()
        return re.search(r"\bYES\b", ans) is not None
    except LLMError:
        return True  # при сбое проверки не блокируем генерацию


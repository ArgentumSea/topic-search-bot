"""Двуязычное расширение поискового запроса (RU + EN)."""
import logging
import re

log = logging.getLogger(__name__)

_CYR_RE = re.compile(r"[А-Яа-яЁё]")

_PROMPT = (
    "Переведи этот поисковый запрос на английский язык. Сохрани смысл,"
    " имена собственные и термины. Верни только перевод, без пояснений"
    " и кавычек.\n\n{query}"
)


async def expand_query(llm, query: str) -> str:
    """Добавляет англ. версию запроса для охвата всего интернета."""
    query = query.strip()
    if (
        not _CYR_RE.search(query)
        or "http" in query
        or " / " in query  # уже расширен
    ):
        return query
    try:
        en = (await llm.generate(_PROMPT.format(query=query))).strip()
        en = en.strip('"').strip()
        if not en or en.lower() == query.lower():
            return query
        return f"{query} / {en}"
    except Exception as exc:  # noqa: BLE001
        log.warning("query expand failed", extra={"error": str(exc)[:120]})
        return query

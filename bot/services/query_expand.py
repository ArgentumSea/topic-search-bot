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


async def refine_query(llm, query: str) -> str:
    """Короткий/размытый запрос -> точный поисковый запрос через LLM."""
    q = query.strip()
    if len(q) >= 40 or "http" in q:
        return q
    prompt = (
        "Переформулируй запрос пользователя в точный поисковый запрос для поисковой системы."
        " Уточни тему, добавь важный контекст, ключевые термины и сущности."
        " Сохрани смысл и имена собственные. Верни только итоговый запрос одной строкой, без пояснений и кавычек."
        "\n\nЗапрос: {query}".format(query=q)
    )
    try:
        refined = (await llm.generate(prompt)).strip().strip(chr(34)).strip()
        if refined.startswith(chr(123)):
            try:
                import json as _json
                _d = _json.loads(refined)
                if isinstance(_d, dict):
                    refined = str(_d.get("search_query") or next(iter(_d.values()), chr(34)*2))
            except Exception:
                pass
        refined = refined.replace(chr(10), chr(32)).strip()
        if refined and len(refined) >= 10:
            log.info("query refined", extra={"from": q, "to": refined})
            return refined
    except Exception as exc:  # noqa: BLE001
        log.warning("refine failed", extra={"error": str(exc)[:120]})
    return q

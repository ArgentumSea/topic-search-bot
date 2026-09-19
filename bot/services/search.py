import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


class SearchError(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    content: str


class SearchProvider:
    """Интерфейс источника поиска. Замена провайдера = новая реализация."""

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        raise NotImplementedError


async def _with_retry(func, attempts: int = 3, base_delay: float = 1.0):
    """Retry с экспоненциальным backoff: 1s, 2s, 4s."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts - 1:
                delay = base_delay * (2**attempt)
                log.warning("retry", extra={"attempt": attempt + 1, "delay": delay})
                await asyncio.sleep(delay)
    raise SearchError(f"поиск недоступен: {last_exc}")


class TavilyProvider(SearchProvider):
    def __init__(self, api_key: str, search_depth: str = "advanced") -> None:
        self._api_key = api_key
        self._search_depth = search_depth

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        def _do() -> dict:
            from tavily import TavilyClient  # локальный импорт

            client = TavilyClient(api_key=self._api_key)
            return client.search(
                query=query, max_results=max_results,
                search_depth=self._search_depth,
            )

        data = await _with_retry(lambda: asyncio.to_thread(_do))
        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]
        if not results:
            raise SearchError("источники не найдены")
        log.info("tavily results", extra={"count": len(results)})
        return results

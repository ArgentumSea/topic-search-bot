import asyncio
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s]+")

# Слова-сигналы новостного интента (все падежи/числа). Пополнять здесь.
_NEWS_TRIGGERS = frozenset("""
новость новости новостей новостям новостями новостях новостью
новый новая новое новые нового новой новых новым новыми новом новую
новинка новинки новинок новинке новинку новинкой новинкам новинками новинках
новейший новейшая новейшее новейшие
свежий свежая свежее свежие свежего свежей свежих свежим свежими свежем
свежак свежака свежаке свежаком
актуальный актуальная актуальное актуальные актуального актуальной
актуальных актуальным актуальными актуальном
последний последняя последнее последние последнего последней последних
последним последними последнем
тренд тренды тренда трендов тренде трендам трендами трендах
событие события событий событиям событиями событиях
анонс анонсы анонса анонсов анонсе анонсам анонсами анонсах
релиз релизы релиза релизов релизе
запуск запуски запуска запусков запуске
презентация презентации презентаций презентациям
обновление обновления обновлений обновлениями
вышел вышла вышло вышли
представлен представлена представлено представлены
анонсирован анонсирована анонсировано
сегодня вчера
""".split())

_WORD_RE = re.compile(r"[a-zа-яё0-9]+")


def _resolve_topic(setting: str, query: str) -> str:
    """auto: news при новостных триггерах в запросе, иначе general."""
    if setting != "auto":
        return setting
    words = set(_WORD_RE.findall(query.lower()))
    return "news" if words & _NEWS_TRIGGERS else "general"


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
        except Exception as exc:  # noqa: BLE001 — ретрай любых сетевых ошибок
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
        from bot.config import settings

        urls = _URL_RE.findall(query)
        if urls:
            return await self._extract_urls(urls[:3])

        from tavily import TavilyClient  # локальный импорт — не тянуть без ключа

        client = TavilyClient(api_key=self._api_key)

        topic = _resolve_topic(settings.TAVILY_TOPIC, query)
        def _do() -> dict:
            return client.search(
                query=query,
                max_results=max_results,
                search_depth=self._search_depth,
                topic=topic,
            **({"days": 7} if topic == "news" else {}),
            )

        data = await asyncio.wait_for(
            _with_retry(lambda: asyncio.to_thread(_do)), timeout=75
        )
        all_results = data.get("results", [])
        raw = [r for r in all_results if r.get("score", 1.0) >= settings.TAVILY_MIN_SCORE]
        if not raw and all_results:
            raw = all_results[:5]
            log.warning("score filter emptied results, using top unfiltered")
        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
            )
            for r in raw
        ]
        if not results:
            raise SearchError("по запросу не найдено релевантных источников")
        log.info("tavily results", extra={"count": len(results)})
        return results

    async def _extract_urls(self, urls: list[str]) -> list[SearchResult]:
        from tavily import TavilyClient

        client = TavilyClient(api_key=self._api_key)

        def _do() -> dict:
            return client.extract(urls=urls)

        data = await asyncio.wait_for(
            _with_retry(lambda: asyncio.to_thread(_do)), timeout=75
        )
        results = [
            SearchResult(
                title=r.get("title") or r.get("url", ""),
                url=r.get("url", ""),
                content=(r.get("raw_content") or r.get("content") or "")[:2000],
            )
            for r in data.get("results", [])
            if (r.get("raw_content") or r.get("content"))
        ]
        if not results:
            results = await self._ytdlp_urls(urls)
        if not results:
            raise SearchError(
                "не удалось извлечь содержимое ссылок"
                " (закрытый доступ или защита от ботов)"
            )
        log.info("tavily extract", extra={"count": len(results)})
        return results

    async def _ytdlp_urls(self, urls: list[str]) -> list[SearchResult]:
        """Метаданные (описание, заголовок) через yt-dlp."""

        def _do(url: str) -> dict:
            import yt_dlp

            opts = {"quiet": True, "skip_download": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        out: list[SearchResult] = []
        for url in urls:
            try:
                info = await asyncio.wait_for(asyncio.to_thread(_do, url), timeout=90)
            except Exception as exc:  # noqa: BLE001
                log.warning("yt-dlp failed", extra={"url": url, "error": str(exc)[:200]})
                continue
            text = (info.get("description") or "") or (info.get("title") or "")
            if text:
                out.append(
                    SearchResult(
                        title=info.get("title") or url,
                        url=url,
                        content=text[:2000],
                    )
                )
        log.info("yt-dlp results", extra={"count": len(out)})
        return out


import asyncio
import logging

log = logging.getLogger(__name__)

_DEAD_UNTIL: dict = {}


class LLMError(Exception):
    pass


class QuotaExhaustedError(LLMError):
    """Иссякли бесплатные тиры всех моделей в цепочке."""


class LLMProvider:
    """Интерфейс LLM. Замена/добавление провайдера = новая реализация."""

    async def generate(
        self, prompt: str, system: str | None = None, schema: dict | None = None
    ) -> str:
        raise NotImplementedError


    async def generate_with_file(
        self, prompt: str, file_path: str, mime_type: str,
        system: str | None = None,
    ) -> str:
        raise NotImplementedError


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resourceexhausted" in text


class GeminiProvider(LLMProvider):
    """Gemini с цепочкой fallback: идём по моделям от умной к лёгкой."""

    def __init__(self, api_key: str, models: list[str], api_keys: list | None = None) -> None:
        if not models:
            raise LLMError("LLM_MODELS пуст")
        self._api_key = api_key
        self._client = None
        self._models = models
        self._keys = [k for k in (api_keys or [api_key]) if k]
        self._key_idx = 0
        # сериализация вызовов: защита от RPM-лимитов free tier
        self._lock = __import__("contextlib").AsyncExitStack()

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._keys[self._key_idx])
        return self._client

    async def generate(
        self, prompt: str, system: str | None = None, schema: dict | None = None
    ) -> str:
        async with self._lock:
            return await self._generate_locked(prompt, system, schema)

    async def _generate_locked(
        self, prompt: str, system: str | None = None,
        schema: dict | None = None,
    ) -> str:
        last_quota_exc: Exception | None = None
        import time as _time
        _now = _time.time()
        _saw_quota = False
        if self._keys and _DEAD_UNTIL.get(self._keys[self._key_idx], 0) > _now:
            for _j in range(len(self._keys)):
                if _DEAD_UNTIL.get(self._keys[_j], 0) < _now:
                    self._key_idx = _j
                    self._client = None
                    break
        for model in self._models:
            try:
                return await asyncio.wait_for(
                    self._generate_one(model, prompt, system, schema), timeout=60
                )
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc) or "404" in str(exc).lower() or "not found" in str(exc).lower() or "503" in str(exc).lower() or "unavailable" in str(exc).lower() or isinstance(exc, TimeoutError):
                    last_quota_exc = exc
                    if _is_quota_error(exc) and "PerDay" in str(exc):
                        _saw_quota = True
                    log.warning(
                        "model quota exhausted, trying next",
                        extra={"model": model},
                    )
                    continue
                raise LLMError(str(exc)) from exc
        if _saw_quota and self._ban_and_switch_key(_now):
            log.warning("llm: key blocked 24h, switching", extra={"idx": self._key_idx})
            return await self._generate_locked(prompt, system, schema)
        raise QuotaExhaustedError("Все модели временно недоступны: квота дня или перегрузка. Попробуйте через 10-15 минут")


    def _ban_and_switch_key(self, now: float) -> bool:
        if len(self._keys) < 2:
            return False
        _DEAD_UNTIL[self._keys[self._key_idx]] = now + 86400
        for _j in range(self._key_idx + 1, self._key_idx + 1 + len(self._keys)):
            _cand = _j % len(self._keys)
            if _DEAD_UNTIL.get(self._keys[_cand], 0) < now:
                self._key_idx = _cand
                self._client = None
                return True
        return False

    async def _generate_one(
        self, model: str, prompt: str, system: str | None, schema: dict | None = None
    ) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.5,
            max_output_tokens=8192,
            system_instruction=system,
        )
        response = await asyncio.to_thread(
            self._get_client().models.generate_content,
            model=model,
            contents=prompt,
            config=config,
        )
        text = response.text
        if not text:
            raise LLMError("пустой ответ модели")
        return text

    async def generate_with_file(
        self, prompt: str, file_path: str, mime_type: str,
        system: str | None = None,
    ) -> str:
        async with self._lock:
            return await self._generate_file_locked(
                prompt, file_path, mime_type, system
            )

    async def _generate_file_locked(
        self, prompt: str, file_path: str, mime_type: str,
        system: str | None,
    ) -> str:
        last_quota_exc: Exception | None = None
        import time as _time
        _now = _time.time()
        _saw_quota = False
        if self._keys and _DEAD_UNTIL.get(self._keys[self._key_idx], 0) > _now:
            for _j in range(len(self._keys)):
                if _DEAD_UNTIL.get(self._keys[_j], 0) < _now:
                    self._key_idx = _j
                    self._client = None
                    break
        for model in self._models:
            try:
                return await asyncio.wait_for(
                    self._generate_file_one(
                        model, prompt, file_path, mime_type, system
                    ),
                    timeout=120,
                )
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc) or "404" in str(exc).lower() or "not found" in str(exc).lower() or "503" in str(exc).lower() or "unavailable" in str(exc).lower() or isinstance(exc, TimeoutError):
                    last_quota_exc = exc
                    if _is_quota_error(exc) and "PerDay" in str(exc):
                        _saw_quota = True
                    log.warning(
                        "model quota exhausted, trying next",
                        extra={"model": model},
                    )
                    continue
                raise LLMError(str(exc)) from exc
        if _saw_quota and self._ban_and_switch_key(_now):
            log.warning("llm: key blocked 24h, switching (file)", extra={"idx": self._key_idx})
            return await self._generate_file_locked(prompt, file_path, mime_type, system)
        raise QuotaExhaustedError("Все модели временно недоступны: квота дня или перегрузка. Попробуйте через 10-15 минут")

    async def _generate_file_one(
        self, model: str, prompt: str, file_path: str, mime_type: str,
        system: str | None,
    ) -> str:
        from google.genai import types

        uploaded = self._get_client().files.upload(
            file=str(file_path),
            config=types.UploadFileConfig(mime_type=mime_type),
        )
        config = types.GenerateContentConfig(
            temperature=0.3, max_output_tokens=2048,
            system_instruction=system,
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=model,
            contents=[uploaded, prompt],
            config=config,
        )
        text = response.text
        if not text:
            raise LLMError("пустой ответ модели")
        return text.strip()

import asyncio
import logging

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class QuotaExhaustedError(LLMError):
    """Иссякли бесплатные тиры всех моделей в цепочке."""


class LLMProvider:
    """Интерфейс LLM. Замена/добавление провайдера = новая реализация."""

    async def generate(self, prompt: str, system: str | None = None) -> str:
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

    def __init__(self, api_key: str, models: list[str]) -> None:
        if not models:
            raise LLMError("LLM_MODELS пуст")
        self._api_key = api_key
        self._client = None
        self._models = models
        # сериализация вызовов: защита от RPM-лимитов free tier
        self._lock = asyncio.Lock()

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str, system: str | None = None) -> str:
        async with self._lock:
            return await self._generate_locked(prompt, system)

    async def _generate_locked(
        self, prompt: str, system: str | None = None
    ) -> str:
        last_quota_exc: Exception | None = None
        for model in self._models:
            try:
                return await self._generate_one(model, prompt, system)
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc):
                    last_quota_exc = exc
                    log.warning(
                        "model quota exhausted, trying next",
                        extra={"model": model},
                    )
                    continue
                raise LLMError(str(exc)) from exc
        raise QuotaExhaustedError(str(last_quota_exc))

    async def _generate_one(
        self, model: str, prompt: str, system: str | None
    ) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=4096,
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
        for model in self._models:
            try:
                return await self._generate_file_one(
                    model, prompt, file_path, mime_type, system
                )
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc):
                    last_quota_exc = exc
                    log.warning(
                        "model quota exhausted, trying next",
                        extra={"model": model},
                    )
                    continue
                raise LLMError(str(exc)) from exc
        raise QuotaExhaustedError(str(last_quota_exc))

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

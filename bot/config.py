from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str
    ADMIN_IDS: list[int] = []

    GEMINI_API_KEY: str
    GEMINI_API_KEY_2: str = ""
    TAVILY_API_KEY: str = ""
    LLM_MODELS: list[str] = ["gemini-3-flash", "gemini-3.1-flash-lite"]
    TAVILY_MAX_RESULTS: int = 10
    TAVILY_SEARCH_DEPTH: str = "advanced"
    TAVILY_MONTHLY_BUDGET: int = 1000
    TAVILY_MIN_SCORE: float = 0.4
    TAVILY_TOPIC: str = "auto"
    TAVILY_CREDITS_PER_REQUEST: int = 2
    TAVILY_RESET_DAY: int = 19
    TAVILY_INITIAL_CREDITS: int = 20

    DATABASE_PATH: str = "/data/topic_search.db"
    REQUESTS_LIMIT_DEFAULT: int = 10
    VOSK_MODEL_PATH: str = "/models/vosk-model-small-ru-0.22"

    USE_WEBHOOK: bool = False
    WEBHOOK_URL: str = ""
    WEBHOOK_PORT: int = 8080
    HEALTHCHECK_PORT: int = 8080

    LOG_LEVEL: str = "INFO"


settings = Settings()

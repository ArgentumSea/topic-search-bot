from pathlib import Path

PROMPTS_DIR = Path("prompts")


def load_prompt(name: str) -> str:
    """Загружает промпт из prompts/{name}.txt. Файл правится на сервере."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")

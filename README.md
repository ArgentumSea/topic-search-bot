# Topic Search (@topic1search_bot)

Telegram-бот для поиска актуальной информации в сети и генерации
постов для Telegram-блога. Поиск — Tavily, генерация — Google Gemini
(бесплатный тир, цепочка fallback между моделями), голосовые —
офлайн-транскрипция Vosk.

## Возможности

- `/search` — подборка актуальных тем по запросу (10+ источников),
  выбор темы кнопкой, генерация поста.
- `/topic` — пост по готовой теме без этапа подборки.
- Приём файлов: фото, видео (до 20 МБ), документы → анализ через Gemini;
  голосовые/аудио → транскрипция Vosk.
- Пост: Telegram HTML-разметка, эмодзи, хештеги, источники;
  длинные темы — серией постов с цепляющим финалом.
- Отдельное сообщение с рекомендацией по медиаконтенту.
- Роли: администратор / ассистент / пользователь.
- `/admin` — управление пользователями, `/stat` — статистика запросов.
- Дневной лимит запросов (по умолчанию 10) для обычных пользователей.

## Требования

- Docker с плагином compose (установка скриптом `deploy/install.sh`).
- Токен бота: создать у @BotFather.
- Ключи API: Google AI Studio (Gemini), Tavily.

## Установка на сервер (Debian 12, root)

```bash
git clone https://github.com/username/topic-search-bot.git /opt/topic-search-bot
cd /opt/topic-search-bot
./deploy/install.sh   # ставит Docker, собирает и запускает бота
```

Перед запуском отредактируй `.env` (создаётся из `.env.example`).

## Переменные окружения

| Переменная | Описание |
|---|---|
| BOT_TOKEN | токен от @BotFather (обязательно) |
| ADMIN_IDS | tg_id админов через запятую (обязательно) |
| GEMINI_API_KEY | ключ Google AI Studio |
| TAVILY_API_KEY | ключ Tavily |
| LLM_MODELS | цепочка моделей fallback (через запятую) |
| REQUESTS_LIMIT_DEFAULT | дневной лимит для пользователей (10) |
| TAVILY_MAX_RESULTS | число источников на запрос (10) |
| TAVILY_SEARCH_DEPTH | basic или advanced (advanced тратит кредиты free tier быстрее) |
| USE_WEBHOOK | false = polling, true = webhook |
| VOSK_MODEL_PATH | путь к модели Vosk в контейнере |

## Заметки

- Свободный текст без команды = запрос на поиск (не обязательно /search).
- Webhook-режим (`USE_WEBHOOK=true`) требует HTTPS: Telegram не принимает
  plain-HTTP webhook-URL — нужен TLS-прокси (nginx/caddy) перед контейнером.
  Для личного бота оставьте polling.
- Если добавите бота в группу, команды будут работать у всех участников;
  отключите это в @BotFather: /setprivacy -> Disable.

## Обновление (патчи через GitHub)

```bash
cd /opt/topic-search-bot
./deploy/update.sh    # git pull + пересборка + рестарт
```

## Правка промптов

Промпты — файлы в `prompts/` (`topics.txt`, `post.txt`, `file.txt`).
Правятся на сервере в терминале:

```bash
cd /opt/topic-search-bot
nano prompts/post.txt
./deploy/update.sh    # или: docker compose restart bot (промпты не требуют сборки)
```

## Структура проекта

```
bot/
  main.py            # точка входа: polling/webhook, healthcheck
  config.py          # pydantic-settings, все из env
  db.py              # SQLite: users, requests_log, topics_cache, posts
  middlewares.py     # блокировка, инъекция db_user
  utils.py           # резка длинных сообщений (plain + HTML)
  services/
    search.py        # SearchProvider + TavilyProvider (retry/backoff)
    llm.py           # LLMProvider + GeminiProvider (fallback-цепочка)
    voice.py         # Vosk-транскрипция
    posting.py       # генерация и оформление постов
    prompts.py       # загрузка промптов
  handlers/
    start.py         # /start, /help
    search.py        # /search, /cancel
    posts.py         # /topic, выбор темы
    files.py         # фото/видео/документы/голосовые
    admin.py         # /admin, /stat
    pipeline.py      # общий пайплайн поиска/поста
    common.py        # FSM-состояния, общие хелперы
prompts/             # промпты (правятся на сервере)
deploy/              # install.sh, update.sh
```

## Мониторинг

- Логи: `docker compose logs -f bot` (JSON-логи).
- Healthcheck: `curl http://localhost:8080/health`.
- При иссякании квот Gemini бот отвечает «Лимит API иссяк».

## Ограничения

- Лимит Bot API на скачивание файлов — 20 МБ.
- Квота Gemini free tier — на проект, а не на ключ.
- Бесплатный тир Gemini: запросы могут использоваться Google
  для обучения моделей.

#!/usr/bin/env bash
# Первичная установка на Debian 12 (root).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
    echo ">>> Устанавливаю Docker..."
    curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
    cp .env.example .env
fi
if ! grep -q "^BOT_TOKEN=123456:ABC" .env 2>/dev/null \
   && ! grep -q "^ADMIN_IDS=123456789" .env 2>/dev/null; then
    : # .env уже заполнен — продолжаем
else
    echo ">>> ЗАПОЛНИ .env: BOT_TOKEN, ADMIN_IDS, GEMINI_API_KEY, TAVILY_API_KEY"
    if [ -t 0 ]; then
        nano .env
    else
        echo ">>> (нет терминала) отредактируй .env и запусти скрипт снова"
        exit 1
    fi
fi

mkdir -p data

echo ">>> Сборка и запуск..."
docker compose up -d --build

echo ">>> Готово. Логи: docker compose logs -f bot"
echo ">>> Healthcheck: curl http://localhost:8080/health"

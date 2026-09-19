#!/usr/bin/env bash
# Обновление бота из репозитория (патчи).
set -euo pipefail

cd "$(dirname "$0")/.."

echo ">>> git pull"
git pull

echo ">>> Пересборка и рестарт"
docker compose up -d --build

echo ">>> Очистка старых образов"
docker image prune -f

echo ">>> Готово."

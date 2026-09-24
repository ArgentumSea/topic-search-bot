#!/bin/bash
# Watchdog: проверяет health; при падении - диагностика, рестарт + сообщение админу
cd /opt/topic-search-bot
if ! curl -sf --max-time 10 http://localhost:8081/health >/dev/null 2>&1; then
    {
        echo "=== date: $(date)"
        echo "=== container:"
        docker ps -a --filter name=topic-search-bot --format "{{.Status}}"
        echo "=== last logs:"
        docker-compose logs --tail 40 bot 2>&1
    } > data/watchdog_last.log
    /usr/local/bin/docker-compose restart bot >/dev/null 2>&1
    TOKEN=$(grep '^BOT_TOKEN=' .env | cut -d= -f2-)
    ADMIN=$(grep '^ADMIN_IDS=' .env | grep -o '[0-9]\+' | head -1)
    if [ -n "$TOKEN" ] && [ -n "$ADMIN" ]; then
        curl -s --max-time 10 -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${ADMIN}" \
            --data-urlencode "text=⚠️ Topic Search не отвечал на healthcheck - выполнен автоматический перезапуск. Диагностика: cat data/watchdog_last.log" >/dev/null 2>&1
    fi
fi

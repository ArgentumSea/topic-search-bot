FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg wget unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir yt-dlp

# Модель Vosk small (RU) для офлайн-транскрипции
RUN mkdir -p /models \
    && wget -q https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip -O /models/vosk.zip \
    && unzip -q /models/vosk.zip -d /models \
    && rm /models/vosk.zip

COPY bot ./bot
COPY prompts ./prompts

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]

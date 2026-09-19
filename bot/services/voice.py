import asyncio
import json
import logging
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger(__name__)


class VoiceTranscriber:
    """Офлайн-транскрипция через Vosk. Модель грузится один раз."""

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None
        # один поток: Vosk Model не гарантированно потокобезопасен
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vosk")

    def _get_model(self):
        if self._model is None:
            from vosk import Model

            log.info("loading vosk model", extra={"path": self._model_path})
            self._model = Model(self._model_path)
        return self._model

    async def transcribe(self, ogg_path: str) -> str:
        """ogg/oga/opus -> текст. Блокирующий Vosk — в выделенном потоке."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._transcribe_sync, ogg_path
        )

    def _transcribe_sync(self, ogg_path: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "voice.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path,
                 "-ar", "16000", "-ac", "1", str(wav_path)],
                check=True, capture_output=True,
            )
            from vosk import KaldiRecognizer, SetLogLevel

            SetLogLevel(-1)
            model = self._get_model()
            with wave.open(str(wav_path), "rb") as wf:
                rec = KaldiRecognizer(model, wf.getframerate())
                while True:
                    data = wf.readframes(4000)
                    if not data:
                        break
                    rec.AcceptWaveform(data)
            result = json.loads(rec.FinalResult())
            text = result.get("text", "").strip()
            log.info("voice transcribed", extra={"chars": len(text)})
            return text

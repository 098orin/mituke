import audioop
import json
import time

from discord.ext.voice_recv import AudioSink
from rich.console import Console
from vosk import KaldiRecognizer, Model

console = Console()


model = Model("models/vosk-model-small-ja")


class VoskSink(AudioSink):
    def __init__(self, text_channel):
        self.text_channel = text_channel
        self.recognizers = {}
        self.messages = {}
        self.last_edit = {}

    def wants_opus(self) -> bool:
        return False  # We want PCM

    def write(self, user, data):
        if user is None:
            return

        if user.bot:
            return

        if user.id not in self.recognizers:
            self.recognizers[user.id] = KaldiRecognizer(model, 16000)

        rec = self.recognizers[user.id]

        # Convert 48kHz stereo → mono
        mono = audioop.tomono(data.pcm, 2, 0.5, 0.5)

        # Resample 48kHz → 16kHz
        resampled, _ = audioop.ratecv(mono, 2, 1, 48000, 16000, None)

        if rec.AcceptWaveform(resampled):
            result = json.loads(rec.Result())
            text = result.get("text", "")
            if text:
                self._final(user, text)
        else:
            partial = json.loads(rec.PartialResult())
            text = partial.get("partial", "")
            if text:
                self._partial(user, text)

    def _partial(self, user, text):
        now = time.time()
        if user.id not in self.last_edit:
            self.last_edit[user.id] = 0

        # Limit edits to once per second
        if now - self.last_edit[user.id] < 1:
            return

        self.last_edit[user.id] = now

        async def edit():
            if user.id not in self.messages:
                self.messages[user.id] = await self.text_channel.send(
                    f"{user.display_name}: {text}"
                )
            else:
                await self.messages[user.id].edit(
                    content=f"{user.display_name}: {text}"
                )

        import asyncio

        asyncio.create_task(edit())

    def _final(self, user, text):
        async def send():
            await self.text_channel.send(f"{user.display_name}: {text}")
            self.messages.pop(user.id, None)

        import asyncio

        asyncio.create_task(send())

    def cleanup(self):
        pass

from __future__ import annotations

import asyncio
import json
import unittest
from array import array
from types import SimpleNamespace
from unittest.mock import patch

from mituke.transcription.sink import TranscriptionSink
from mituke.transcription.state import MessageState, RecognitionTask


class FakeRecognizer:
    def __init__(self) -> None:
        self.accept_waveform = False
        self.result_text = ""
        self.partial_text = "あとから"
        self.final_text = ""
        self.accept_calls: list[bytes] = []

    def AcceptWaveform(self, pcm: bytes) -> bool:
        self.accept_calls.append(pcm)
        return self.accept_waveform

    def Result(self) -> str:
        return json.dumps({"text": self.result_text})

    def PartialResult(self) -> str:
        return json.dumps({"partial": self.partial_text})

    def FinalResult(self) -> str:
        return json.dumps({"text": self.final_text})


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[str] = []
        self.deleted = False

    async def edit(self, *, content: str) -> None:
        self.content = content
        self.edits.append(content)

    async def delete(self) -> None:
        self.deleted = True


class FakeTextChannel:
    def __init__(self) -> None:
        self.messages: list[FakeMessage] = []

    async def send(self, content: str) -> FakeMessage:
        message = FakeMessage(content)
        self.messages.append(message)
        return message


class FakeRecognizerFactory:
    def __init__(self) -> None:
        self.instances: list[FakeRecognizer] = []

    def create(self) -> FakeRecognizer:
        recognizer = FakeRecognizer()
        self.instances.append(recognizer)
        return recognizer


class TranscriptionSinkTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_pcm(sample: int, samples: int = 320) -> bytes:
        return array("h", [sample] * samples).tobytes()

    async def asyncSetUp(self) -> None:
        self.text_channel = FakeTextChannel()
        self.loop = asyncio.get_running_loop()
        self.recognizer_factory = FakeRecognizerFactory()

        self.audio_patcher = patch(
            "mituke.transcription.sink.convert_pcm_48khz_stereo_to_16khz_mono",
            side_effect=lambda pcm, state=None: (pcm, state),
        )

        self.audio_patcher.start()

    async def asyncTearDown(self) -> None:
        self.audio_patcher.stop()

    async def test_request_stop_finalizes_pending_message(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )
        message = FakeMessage("Alice: 話し始めました。文字起こしを始めます…")
        sink.message_states[1] = MessageState(
            message=message,
            last_content=message.content,
        )
        with sink.state_lock:
            state = sink._get_or_create_state(1, "Alice")
            state.committed_texts.append("こんにちは")

        sink.request_stop()
        await sink.wait_closed()

        self.assertEqual(message.edits, ["Alice: こんにちは"])
        self.assertNotIn(1, sink.message_states)
        self.assertTrue(sink.worker_task.done())

    async def test_cleanup_ignores_late_audio_after_shutdown(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )

        sink.cleanup()
        await sink.wait_closed()

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        sink.write(user, SimpleNamespace(pcm=b"pcm"))
        sink.on_voice_member_speaking_start(user)
        await asyncio.sleep(0)

        self.assertEqual(sink.recognition_states, {})
        self.assertEqual(self.text_channel.messages, [])

    async def test_buffers_audio_until_user_mapping_is_available(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )

        lead_pcm = self._make_pcm(900)
        tail_pcm = self._make_pcm(1400)

        sink.write(None, SimpleNamespace(pcm=lead_pcm, packet=SimpleNamespace(ssrc=42)))

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        sink.write(
            user,
            SimpleNamespace(pcm=tail_pcm, packet=SimpleNamespace(ssrc=42)),
        )
        sink.write(
            user,
            SimpleNamespace(pcm=tail_pcm, packet=SimpleNamespace(ssrc=42)),
        )

        await asyncio.wait_for(sink.wait_for_idle(), 1)

        state = sink.recognition_states[1]
        recognizer = state.recognizer

        self.assertEqual(recognizer.accept_calls, [lead_pcm + tail_pcm + tail_pcm])
        self.assertEqual(len(self.text_channel.messages), 1)
        self.assertEqual(self.text_channel.messages[0].content, "Alice: あとから")
        self.assertEqual(
            self.text_channel.messages[0].edits,
            ["Alice: あとから"],
        )

        sink.request_stop()
        await sink.wait_closed()

    async def test_start_event_and_audio_fallback_do_not_duplicate_message(
        self,
    ) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        sink.on_voice_member_speaking_start(user)
        sink.write(
            user,
            SimpleNamespace(
                pcm=self._make_pcm(1200),
                packet=SimpleNamespace(ssrc=42),
            ),
        )
        sink.write(
            user,
            SimpleNamespace(
                pcm=self._make_pcm(1200),
                packet=SimpleNamespace(ssrc=42),
            ),
        )
        sink.write(
            user,
            SimpleNamespace(
                pcm=self._make_pcm(1200),
                packet=SimpleNamespace(ssrc=42),
            ),
        )

        await asyncio.wait_for(sink.wait_for_idle(), 1)

        self.assertEqual(len(self.text_channel.messages), 1)
        self.assertEqual(self.text_channel.messages[0].content, "Alice: あとから")
        self.assertEqual(
            self.text_channel.messages[0].edits,
            ["Alice: あとから"],
        )

        sink.request_stop()
        await sink.wait_closed()

    async def test_silent_audio_does_not_trigger_start_message(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
            speech_stop_grace_period=0.01,
        )

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        sink.on_voice_member_speaking_start(user)
        sink.write(
            user,
            SimpleNamespace(pcm=self._make_pcm(0), packet=SimpleNamespace(ssrc=42)),
        )
        sink.on_voice_member_speaking_stop(user)

        await asyncio.sleep(0.03)
        await asyncio.wait_for(sink.wait_for_idle(), 1)

        self.assertEqual(self.text_channel.messages, [])
        self.assertEqual(sink.recognition_states, {})

        sink.request_stop()
        await sink.wait_closed()

    async def test_brief_noise_does_not_trigger_transcription_start(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        noise = SimpleNamespace(
            pcm=self._make_pcm(350),
            packet=SimpleNamespace(ssrc=42),
        )
        speech = SimpleNamespace(
            pcm=self._make_pcm(1200),
            packet=SimpleNamespace(ssrc=42),
        )

        sink.write(user, noise)
        await asyncio.wait_for(sink.wait_for_idle(), 1)

        recognizer = sink.recognition_states[1].recognizer
        self.assertEqual(recognizer.accept_calls, [])
        self.assertEqual(self.text_channel.messages, [])

        sink.write(user, speech)
        sink.write(user, speech)
        sink.write(user, speech)
        await asyncio.wait_for(sink.wait_for_idle(), 1)

        self.assertEqual(recognizer.accept_calls, [speech.pcm * 3])
        self.assertEqual(len(self.text_channel.messages), 1)
        self.assertEqual(self.text_channel.messages[0].content, "Alice: あとから")

        sink.request_stop()
        await sink.wait_closed()

    async def test_brief_pause_keeps_same_transcription_session(self) -> None:
        sink = TranscriptionSink(
            text_channel=self.text_channel,
            recognizer=self.recognizer_factory,
            loop=self.loop,
        )

        user = SimpleNamespace(id=1, display_name="Alice", bot=False)
        audio = SimpleNamespace(
            pcm=self._make_pcm(1600), packet=SimpleNamespace(ssrc=42)
        )

        sink.write(user, audio)
        sink.write(user, audio)
        sink.write(user, audio)
        await asyncio.wait_for(sink.wait_for_idle(), 1)
        previous_token = sink.recognition_states[1].activity_token

        sink.on_voice_member_speaking_stop(user)
        sink.on_voice_member_speaking_start(user)
        sink.write(user, audio)
        await asyncio.wait_for(sink.wait_for_idle(), 1)

        sink._process_stop_timeout(
            RecognitionTask(
                kind="stop_timeout",
                user_id=1,
                display_name="Alice",
                token=previous_token,
            )
        )
        await asyncio.wait_for(sink.wait_for_idle(), 1)

        self.assertEqual(len(self.text_channel.messages), 1)
        self.assertIn(1, sink.message_states)
        self.assertIn(1, sink.recognition_states)

        sink.request_stop()
        await sink.wait_closed()

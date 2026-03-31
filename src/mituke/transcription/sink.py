from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any

import discord
from discord.ext.voice_recv import AudioSink
from discord.opus import Decoder
from rich.console import Console

from mituke.transcription.audio import (
    PCM_SAMPLE_WIDTH_BYTES,
    VOICE_ACTIVITY_START_MIN_FRAMES,
    VOSK_SAMPLE_RATE,
    convert_pcm_48khz_stereo_to_16khz_mono,
    trim_leading_silence,
)
from mituke.transcription.messages import TranscriptMessagePublisher
from mituke.transcription.pending_audio import PendingAudioBuffer
from mituke.transcription.recognizer import SpeechRecognizer
from mituke.transcription.state import RecognitionState, RecognitionTask, SinkEvent
from mituke.transcription.text import join_transcript_parts, normalize_transcript

console = Console()
PENDING_AUDIO_RETENTION_SECONDS = 1.0
PENDING_AUDIO_MAX_BYTES = (
    Decoder.SAMPLING_RATE * Decoder.CHANNELS * PCM_SAMPLE_WIDTH_BYTES
)
SPEECH_STOP_GRACE_SECONDS = 0.8
PRE_START_BUFFER_MAX_BYTES = VOSK_SAMPLE_RATE * PCM_SAMPLE_WIDTH_BYTES


class TranscriptionSink(AudioSink):
    def __init__(
        self,
        text_channel: discord.abc.Messageable,
        recognizer: SpeechRecognizer,
        loop: asyncio.AbstractEventLoop,
        partial_update_interval: float = 1.0,
        speech_stop_grace_period: float = SPEECH_STOP_GRACE_SECONDS,
    ) -> None:
        super().__init__()
        self.text_channel = text_channel
        self.loop = loop
        self.recognizer = recognizer
        self.partial_update_interval = partial_update_interval
        self.speech_stop_grace_period = speech_stop_grace_period
        self.recognition_states: dict[int, RecognitionState] = {}
        self.message_publisher = TranscriptMessagePublisher(text_channel)
        self.message_states = self.message_publisher.message_states
        self.pending_audio = PendingAudioBuffer(
            retention_seconds=PENDING_AUDIO_RETENTION_SECONDS,
            max_bytes=PENDING_AUDIO_MAX_BYTES,
        )
        self.state_lock = threading.Lock()
        self.shutdown_requested = threading.Event()
        self.processing_queue: queue.Queue[RecognitionTask] = queue.Queue()
        self.processor_thread = threading.Thread(
            target=self._recognition_worker,
            name="mituke-recognition",
            daemon=True,
        )
        self.event_queue: asyncio.Queue[SinkEvent] = asyncio.Queue()
        self.worker_task = loop.create_task(self._event_worker())
        self.processor_thread.start()

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.User | discord.Member | None, data: Any) -> None:
        if self.shutdown_requested.is_set() or (user is not None and user.bot):
            return

        pcm = getattr(data, "pcm", None)
        if not pcm:
            return

        packet = getattr(data, "packet", None)
        self._enqueue_recognition_task(
            RecognitionTask(
                kind="audio",
                user_id=None if user is None else user.id,
                display_name="" if user is None else user.display_name,
                pcm=pcm,
                ssrc=getattr(packet, "ssrc", None),
                received_at=time.monotonic(),
            )
        )

    @AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member) -> None:
        if self.shutdown_requested.is_set() or member.bot:
            return

        self._enqueue_recognition_task(
            RecognitionTask(
                kind="start",
                user_id=member.id,
                display_name=member.display_name,
            )
        )

    @AudioSink.listener()
    def on_voice_member_speaking_stop(self, member: discord.Member) -> None:
        if self.shutdown_requested.is_set() or member.bot:
            return

        self._enqueue_recognition_task(
            RecognitionTask(
                kind="stop",
                user_id=member.id,
                display_name=member.display_name,
            )
        )

    def _recognition_worker(self) -> None:
        while True:
            task = self.processing_queue.get()
            try:
                if task.kind == "shutdown":
                    self._finalize_all_sessions()
                    self._queue_event("shutdown", 0, "", "")
                    return
                if task.kind == "start":
                    self._process_start(task)
                elif task.kind == "stop":
                    self._process_stop(task)
                elif task.kind == "audio":
                    self._process_audio(task)
                elif task.kind == "stop_timeout":
                    self._process_stop_timeout(task)
            except Exception as error:
                console.log(
                    f"音声認識ワーカーで予期しないエラーが発生しました: {error}"
                )
            finally:
                self.processing_queue.task_done()

    def _process_start(self, task: RecognitionTask) -> None:
        if task.user_id is None:
            return

        with self.state_lock:
            current_state = self._get_or_create_state(task.user_id, task.display_name)
            current_state.display_name = task.display_name
            current_state.activity_token += 1

    def _process_stop(self, task: RecognitionTask) -> None:
        if task.user_id is None:
            return

        with self.state_lock:
            current_state = self.recognition_states.get(task.user_id)
            if current_state is None:
                return

            current_state.display_name = task.display_name or current_state.display_name
            if (
                not current_state.start_announced
                and not current_state.committed_texts
                and not current_state.partial_text
            ):
                self.recognition_states.pop(task.user_id, None)
                return

            token = current_state.activity_token
            display_name = current_state.display_name

        self._schedule_stop_timeout(task.user_id, display_name, token)

    def _process_audio(self, task: RecognitionTask) -> None:
        if task.user_id is None:
            with self.state_lock:
                self.pending_audio.remember(task.ssrc, task.pcm, task.received_at)
            return

        should_queue_start = False
        display_text = ""
        with self.state_lock:
            pending_audio = self.pending_audio.take(task.ssrc, task.received_at)
            current_state = self._get_or_create_state(task.user_id, task.display_name)
            current_state.display_name = task.display_name
            current_state.activity_token += 1

            mono_16khz_pcm, current_state.resample_state = (
                convert_pcm_48khz_stereo_to_16khz_mono(
                    pending_audio + task.pcm,
                    current_state.resample_state,
                )
            )
            if not mono_16khz_pcm:
                return

            if not current_state.start_announced:
                current_state.pre_start_pcm.extend(mono_16khz_pcm)
                if len(current_state.pre_start_pcm) > PRE_START_BUFFER_MAX_BYTES:
                    del current_state.pre_start_pcm[:-PRE_START_BUFFER_MAX_BYTES]

                mono_16khz_pcm = trim_leading_silence(
                    bytes(current_state.pre_start_pcm),
                    min_voiced_frames=VOICE_ACTIVITY_START_MIN_FRAMES,
                    preroll_frames=0,
                )
                if not mono_16khz_pcm:
                    return

                current_state.pre_start_pcm.clear()
                current_state.start_announced = True
                should_queue_start = True

            if current_state.recognizer.AcceptWaveform(mono_16khz_pcm):
                result = json.loads(current_state.recognizer.Result())
                text = normalize_transcript(result.get("text", ""))
                if not text:
                    if should_queue_start:
                        self._queue_event("start", task.user_id, task.display_name, "")
                    return

                current_state.committed_texts.append(text)
                current_state.partial_text = ""
                display_text = join_transcript_parts(current_state.committed_texts)
            else:
                partial_result = json.loads(current_state.recognizer.PartialResult())
                partial_text = normalize_transcript(partial_result.get("partial", ""))
                if not partial_text:
                    if should_queue_start:
                        self._queue_event("start", task.user_id, task.display_name, "")
                    return

                current_state.partial_text = partial_text
                if (
                    task.received_at - current_state.last_partial_sent_at
                    >= self.partial_update_interval
                ):
                    current_state.last_partial_sent_at = task.received_at
                    display_text = join_transcript_parts(
                        [*current_state.committed_texts, current_state.partial_text]
                    )

        if should_queue_start:
            self._queue_event("start", task.user_id, task.display_name, "")

        if display_text:
            self._queue_event("update", task.user_id, task.display_name, display_text)

    def _process_stop_timeout(self, task: RecognitionTask) -> None:
        if task.user_id is None:
            return

        with self.state_lock:
            current_state = self.recognition_states.get(task.user_id)
            if current_state is None or current_state.activity_token != task.token:
                return

            display_name = current_state.display_name

        final_text = self._finalize_user_session(task.user_id)
        self._queue_event("finalize", task.user_id, display_name, final_text)

    async def _event_worker(self) -> None:
        while True:
            event = await self.event_queue.get()
            try:
                if event.kind == "shutdown":
                    return
                if self.shutdown_requested.is_set() and event.kind not in {
                    "finalize",
                    "shutdown",
                }:
                    continue
                if event.kind == "start":
                    await self.message_publisher.handle_start(event)
                elif event.kind == "update":
                    await self.message_publisher.handle_update(event)
                elif event.kind == "finalize":
                    await self.message_publisher.handle_finalize(event)
            except discord.HTTPException as error:
                console.log(f"Discord への送信に失敗しました: {error}")
            except Exception as error:
                console.log(f"文字起こし処理で予期しないエラーが発生しました: {error}")
            finally:
                self.event_queue.task_done()

    def _get_or_create_state(self, user_id: int, display_name: str) -> RecognitionState:
        current_state = self.recognition_states.get(user_id)
        if current_state is None:
            current_state = RecognitionState(
                recognizer=self.recognizer.create(),
                display_name=display_name,
            )
            self.recognition_states[user_id] = current_state

        return current_state

    def _finalize_user_session(self, user_id: int) -> str:
        with self.state_lock:
            current_state = self.recognition_states.pop(user_id, None)

        if current_state is None:
            return ""

        final_result = json.loads(current_state.recognizer.FinalResult())
        final_text = normalize_transcript(final_result.get("text", ""))
        if final_text:
            current_state.committed_texts.append(final_text)

        return join_transcript_parts(current_state.committed_texts)

    def _queue_event(
        self,
        kind: str,
        user_id: int,
        display_name: str,
        text: str,
    ) -> None:
        if self.shutdown_requested.is_set() and kind not in {"finalize", "shutdown"}:
            return

        event = SinkEvent(
            kind=kind,
            user_id=user_id,
            display_name=display_name,
            text=text,
        )
        try:
            self.loop.call_soon_threadsafe(self.event_queue.put_nowait, event)
        except RuntimeError:
            console.log(
                "イベントループ停止後のため、文字起こしイベントを破棄しました。"
            )

    def _enqueue_recognition_task(self, task: RecognitionTask) -> None:
        if self.shutdown_requested.is_set() and task.kind != "shutdown":
            return

        self.processing_queue.put_nowait(task)

    def _schedule_stop_timeout(
        self,
        user_id: int,
        display_name: str,
        token: int,
    ) -> None:
        try:
            self.loop.call_soon_threadsafe(
                self.loop.call_later,
                self.speech_stop_grace_period,
                self._enqueue_recognition_task,
                RecognitionTask(
                    kind="stop_timeout",
                    user_id=user_id,
                    display_name=display_name,
                    token=token,
                ),
            )
        except RuntimeError:
            console.log(
                "イベントループ停止後のため、発話終了待ちタイマーを破棄しました。"
            )

    def request_stop(self) -> None:
        self._begin_shutdown()

    async def wait_for_idle(self) -> None:
        await asyncio.to_thread(self.processing_queue.join)
        await self.event_queue.join()

    async def wait_closed(self) -> None:
        await asyncio.to_thread(self.processing_queue.join)
        await asyncio.to_thread(self.processor_thread.join)
        await asyncio.shield(self.worker_task)

    def cleanup(self) -> None:
        self._begin_shutdown()

    def _begin_shutdown(self) -> None:
        if self.shutdown_requested.is_set():
            return

        self.shutdown_requested.set()
        self._enqueue_recognition_task(RecognitionTask(kind="shutdown"))

    def _finalize_all_sessions(self) -> None:
        with self.state_lock:
            pending_speakers = [
                (user_id, current_state.display_name)
                for user_id, current_state in self.recognition_states.items()
            ]
            self.pending_audio.clear()

        for user_id, display_name in pending_speakers:
            final_text = self._finalize_user_session(user_id)
            self._queue_event("finalize", user_id, display_name, final_text)

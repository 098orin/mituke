from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

import discord
from vosk import KaldiRecognizer

RecognitionTaskKind: TypeAlias = Literal[
    "audio",
    "start",
    "stop",
    "stop_timeout",
    "shutdown",
]
SinkEventKind: TypeAlias = Literal["start", "update", "finalize", "shutdown"]


@dataclass
class RecognitionState:
    recognizer: KaldiRecognizer
    display_name: str
    committed_texts: list[str] = field(default_factory=list)
    partial_text: str = ""
    last_partial_sent_at: float = 0.0
    resample_state: Any = None
    pre_start_pcm: bytearray = field(default_factory=bytearray)
    start_announced: bool = False
    activity_token: int = 0


@dataclass
class MessageState:
    message: discord.Message | None = None
    last_content: str = ""


@dataclass(frozen=True)
class RecognitionTask:
    kind: RecognitionTaskKind
    user_id: int | None = None
    display_name: str = ""
    pcm: bytes = b""
    ssrc: int | None = None
    received_at: float = 0.0
    token: int = 0


@dataclass(frozen=True)
class SinkEvent:
    kind: SinkEventKind
    user_id: int
    display_name: str
    text: str = ""

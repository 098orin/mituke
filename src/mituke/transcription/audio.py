from __future__ import annotations

from typing import Any

try:
    import audioop
except ModuleNotFoundError:
    import audioop_lts as audioop

from discord.opus import Decoder

PCM_SAMPLE_WIDTH_BYTES = 2
VOSK_SAMPLE_RATE = 16000
VOICE_ACTIVITY_FRAME_MS = 20
VOICE_ACTIVITY_RMS_THRESHOLD = 500
VOICE_ACTIVITY_START_MIN_FRAMES = 3


def _to_mono_pcm(pcm: bytes, channels: int) -> bytes:
    if channels == 1:
        return pcm
    if channels != 2:
        raise ValueError(
            f"unsupported PCM channel count: expected 1 or 2, got {channels}"
        )

    return audioop.tomono(pcm, PCM_SAMPLE_WIDTH_BYTES, 0.5, 0.5)


def _resample_pcm(
    mono_pcm: bytes,
    *,
    input_sample_rate: int,
    output_sample_rate: int,
    state: Any | None,
) -> tuple[bytes, Any | None]:
    if not mono_pcm:
        return b"", state

    if input_sample_rate == output_sample_rate:
        return mono_pcm, state

    return audioop.ratecv(
        mono_pcm,
        PCM_SAMPLE_WIDTH_BYTES,
        1,
        input_sample_rate,
        output_sample_rate,
        state,
    )


def _align_pcm_samples(pcm: bytes, sample_width: int = PCM_SAMPLE_WIDTH_BYTES) -> bytes:
    remainder = len(pcm) % sample_width
    if remainder == 0:
        return pcm

    return pcm[:-remainder]


def has_voice_activity(
    pcm: bytes,
    *,
    sample_width: int = PCM_SAMPLE_WIDTH_BYTES,
    rms_threshold: int = VOICE_ACTIVITY_RMS_THRESHOLD,
) -> bool:
    aligned_pcm = _align_pcm_samples(pcm, sample_width)
    if not aligned_pcm:
        return False

    return audioop.rms(aligned_pcm, sample_width) >= rms_threshold


def trim_leading_silence(
    pcm: bytes,
    *,
    sample_rate: int = VOSK_SAMPLE_RATE,
    sample_width: int = PCM_SAMPLE_WIDTH_BYTES,
    rms_threshold: int = VOICE_ACTIVITY_RMS_THRESHOLD,
    frame_duration_ms: int = VOICE_ACTIVITY_FRAME_MS,
    min_voiced_frames: int = 1,
    preroll_frames: int = 1,
) -> bytes:
    aligned_pcm = _align_pcm_samples(pcm, sample_width)
    if not aligned_pcm:
        return b""

    frame_size = max(
        sample_width,
        sample_rate * sample_width * frame_duration_ms // 1000,
    )
    if frame_size % sample_width != 0:
        frame_size += sample_width - (frame_size % sample_width)

    consecutive_voiced_frames = 0
    first_voiced_offset: int | None = None
    speech_start_offset: int | None = None
    for offset in range(0, len(aligned_pcm), frame_size):
        frame = aligned_pcm[offset : offset + frame_size]
        if has_voice_activity(
            frame,
            sample_width=sample_width,
            rms_threshold=rms_threshold,
        ):
            consecutive_voiced_frames += 1
            if first_voiced_offset is None:
                first_voiced_offset = offset
            if consecutive_voiced_frames >= max(1, min_voiced_frames):
                speech_start_offset = first_voiced_offset
                break
            continue

        consecutive_voiced_frames = 0
        first_voiced_offset = None

    if speech_start_offset is None:
        return b""

    start_offset = max(0, speech_start_offset - (preroll_frames * frame_size))
    return aligned_pcm[start_offset:]


class DiscordPcmConverter:
    def __init__(
        self,
        *,
        input_sample_rate: int = Decoder.SAMPLING_RATE,
        input_channels: int = Decoder.CHANNELS,
        output_sample_rate: int = VOSK_SAMPLE_RATE,
    ) -> None:
        self.input_sample_rate = input_sample_rate
        self.input_channels = input_channels
        self.output_sample_rate = output_sample_rate
        self._ratecv_state: Any | None = None

    def convert(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""

        mono_pcm = _to_mono_pcm(pcm, self.input_channels)
        converted_pcm, self._ratecv_state = _resample_pcm(
            mono_pcm,
            input_sample_rate=self.input_sample_rate,
            output_sample_rate=self.output_sample_rate,
            state=self._ratecv_state,
        )
        return converted_pcm


def convert_pcm_48khz_stereo_to_16khz_mono(
    pcm: bytes,
    state: Any | None = None,
) -> tuple[bytes, Any | None]:
    if not pcm:
        return b"", state

    mono_pcm = _to_mono_pcm(pcm, Decoder.CHANNELS)
    return _resample_pcm(
        mono_pcm,
        input_sample_rate=Decoder.SAMPLING_RATE,
        output_sample_rate=VOSK_SAMPLE_RATE,
        state=state,
    )

from __future__ import annotations

import unittest
from array import array

from mituke.transcription.audio import (
    DiscordPcmConverter,
    convert_pcm_48khz_stereo_to_16khz_mono,
    has_voice_activity,
    trim_leading_silence,
)
from mituke.transcription.text import join_transcript_parts, normalize_transcript


class NormalizeTranscriptTests(unittest.TestCase):
    def test_removes_extra_whitespace(self) -> None:
        self.assertEqual(normalize_transcript("  hello   world  "), "hello world")

    def test_joins_japanese_words_without_spaces(self) -> None:
        self.assertEqual(
            normalize_transcript("  今日は  いい 天気  "), "今日はいい天気"
        )

    def test_keeps_spaces_when_latin_text_is_present(self) -> None:
        self.assertEqual(
            normalize_transcript("  hello   世界  "),
            "hello 世界",
        )

    def test_joins_transcript_parts_and_normalizes_result(self) -> None:
        self.assertEqual(
            join_transcript_parts(["  今日 は ", "", "  いい 天気  "]),
            "今日はいい天気",
        )


class ConvertPcmTests(unittest.TestCase):
    def test_returns_empty_bytes_for_empty_pcm(self) -> None:
        self.assertEqual(
            convert_pcm_48khz_stereo_to_16khz_mono(b""),
            (b"", None),
        )

    def test_converts_48khz_stereo_pcm_to_16khz_mono(self) -> None:
        stereo_samples = array(
            "h",
            [
                3000,
                9000,
                6000,
                12000,
                9000,
                15000,
            ],
        )

        expected = array("h", [6000]).tobytes()

        converted, _ = convert_pcm_48khz_stereo_to_16khz_mono(stereo_samples.tobytes())

        self.assertEqual(converted, expected)

    def test_keeps_resample_state_across_chunks(self) -> None:
        first_chunk = array(
            "h",
            [
                3000,
                9000,
                6000,
                12000,
                9000,
                15000,
            ],
        ).tobytes()
        second_chunk = array(
            "h",
            [
                12000,
                18000,
                15000,
                21000,
                18000,
                24000,
            ],
        ).tobytes()

        combined, _ = convert_pcm_48khz_stereo_to_16khz_mono(first_chunk + second_chunk)
        converted_first, state = convert_pcm_48khz_stereo_to_16khz_mono(first_chunk)
        converted_second, next_state = convert_pcm_48khz_stereo_to_16khz_mono(
            second_chunk,
            state,
        )

        self.assertIsNotNone(next_state)
        self.assertEqual(converted_first + converted_second, combined)

    def test_stream_converter_keeps_resampling_state_across_chunks(self) -> None:
        stereo_samples = array(
            "h",
            [
                1000,
                3000,
                2000,
                4000,
                3000,
                5000,
                4000,
                6000,
                5000,
                7000,
                6000,
                8000,
            ],
        ).tobytes()
        converter = DiscordPcmConverter()

        first_chunk = stereo_samples[:8]
        second_chunk = stereo_samples[8:]

        converted_stream = converter.convert(first_chunk) + converter.convert(
            second_chunk
        )
        converted_full, _ = convert_pcm_48khz_stereo_to_16khz_mono(stereo_samples)

        self.assertEqual(converted_stream, converted_full)

    def test_detects_voice_activity_from_non_silent_pcm(self) -> None:
        silent_pcm = array("h", [0] * 320).tobytes()
        noise_pcm = array("h", [350] * 320).tobytes()
        voiced_pcm = array("h", [1200] * 320).tobytes()

        self.assertFalse(has_voice_activity(silent_pcm))
        self.assertFalse(has_voice_activity(noise_pcm))
        self.assertTrue(has_voice_activity(voiced_pcm))

    def test_trims_leading_silence_before_first_voiced_frame(self) -> None:
        silent_frame = array("h", [0] * 320).tobytes()
        voiced_frame = array("h", [1600] * 320).tobytes()

        trimmed = trim_leading_silence(
            silent_frame + silent_frame + voiced_frame,
            preroll_frames=0,
        )

        self.assertEqual(trimmed, voiced_frame)

    def test_requires_sustained_voice_when_min_voiced_frames_is_set(self) -> None:
        silent_frame = array("h", [0] * 320).tobytes()
        voiced_frame = array("h", [1600] * 320).tobytes()

        trimmed = trim_leading_silence(
            silent_frame + voiced_frame + silent_frame,
            min_voiced_frames=2,
            preroll_frames=0,
        )

        self.assertEqual(trimmed, b"")

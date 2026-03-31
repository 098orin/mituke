from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mituke.bot.commands import show_help, start_listening, stop_listening
from mituke.bot.messages import vosk_model_load_failed
from mituke.config import Settings


class FakeMember:
    def __init__(
        self,
        *,
        member_id: int = 1,
        display_name: str = "Alice",
        voice: object | None = None,
        bot: bool = False,
    ) -> None:
        self.id = member_id
        self.display_name = display_name
        self.voice = voice
        self.bot = bot


class FakeVoiceRecvClient:
    def __init__(
        self,
        *,
        channel: object | None = None,
        listening: bool = False,
        sink: object | None = None,
    ) -> None:
        self.channel = channel
        self._listening = listening
        self.sink = sink
        self.stop_called = False
        self.disconnect_calls: list[bool] = []
        self.move_calls: list[object] = []
        self.listen_calls: list[tuple[object, object]] = []
        self._ssrc_to_id: dict[int, int] = {123: 1}
        self._id_to_ssrc: dict[int, int] = {1: 123}

    def is_listening(self) -> bool:
        return self._listening

    def stop_listening(self) -> None:
        self.stop_called = True
        self._listening = False

    async def disconnect(self, *, force: bool) -> None:
        self.disconnect_calls.append(force)

    async def move_to(self, channel: object) -> None:
        self.move_calls.append(channel)
        self.channel = channel

    def listen(self, sink: object, *, after: object) -> None:
        self.listen_calls.append((sink, after))
        self.sink = sink
        self._listening = True


class FakeOtherVoiceClient:
    def __init__(self) -> None:
        self.disconnect_calls: list[bool] = []

    async def disconnect(self, *, force: bool) -> None:
        self.disconnect_calls.append(force)


class FakeVoiceChannel:
    def __init__(
        self, name: str, connected_client: FakeVoiceRecvClient | None = None
    ) -> None:
        self.name = name
        self.connected_client = connected_client or FakeVoiceRecvClient(channel=self)
        self.connect_calls: list[object] = []

    async def connect(self, *, cls: object) -> FakeVoiceRecvClient:
        self.connect_calls.append(cls)
        self.connected_client.channel = self
        return self.connected_client


class FakeTextChannel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)


class FakeSink:
    def __init__(
        self, *, text_channel: object, recognizer: object, loop: object
    ) -> None:
        self.text_channel = text_channel
        self.recognizer = recognizer
        self.loop = loop


class FakeRecognizerFactory:
    def __init__(self, *, model_path: Path) -> None:
        self.model_path = model_path


class FakeManagedSink:
    def __init__(self) -> None:
        self.request_stop_called = False
        self.wait_closed_called = False

    def request_stop(self) -> None:
        self.request_stop_called = True

    async def wait_closed(self) -> None:
        self.wait_closed_called = True


class FakeGuild:
    def __init__(self, voice_client: object | None = None) -> None:
        self.voice_client = voice_client


class FakeContext:
    def __init__(
        self, *, guild: object | None, author: object, channel: object
    ) -> None:
        self.guild = guild
        self.author = author
        self.channel = channel
        self.sent_messages: list[str] = []

    async def send(self, content: str) -> None:
        self.sent_messages.append(content)


class StartListeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_server_text_channel(self) -> None:
        settings = Settings("token", Path("model"), None)
        ctx = FakeContext(guild=None, author=object(), channel=FakeTextChannel())

        await start_listening(ctx, settings, Mock())

        self.assertEqual(
            ctx.sent_messages,
            ["このコマンドはサーバー内のテキストチャンネルで使ってください。"],
        )

    async def test_requires_author_in_voice_channel(self) -> None:
        settings = Settings("token", Path("model"), None)
        ctx = FakeContext(
            guild=FakeGuild(),
            author=FakeMember(voice=None),
            channel=FakeTextChannel(),
        )

        with patch("mituke.bot.commands.discord.Member", FakeMember):
            await start_listening(ctx, settings, Mock())

        self.assertEqual(
            ctx.sent_messages,
            ["先に参加したいボイスチャンネルへ入ってください。"],
        )

    async def test_disconnects_incompatible_client_then_starts_listening(self) -> None:
        settings = Settings("token", Path("model"), None)
        handle_listen_error = Mock()
        other_client = FakeOtherVoiceClient()
        target_channel = FakeVoiceChannel("General")
        ctx = FakeContext(
            guild=FakeGuild(voice_client=other_client),
            author=FakeMember(voice=SimpleNamespace(channel=target_channel)),
            channel=FakeTextChannel(),
        )
        fake_loop = object()

        with (
            patch("mituke.bot.commands.discord.Member", FakeMember),
            patch("mituke.bot.commands.VoiceRecvClient", FakeVoiceRecvClient),
            patch("mituke.bot.commands.TranscriptionSink", FakeSink),
            patch("mituke.bot.commands.VoskRecognizer", FakeRecognizerFactory),
            patch(
                "mituke.bot.commands.asyncio.get_running_loop", return_value=fake_loop
            ),
        ):
            await start_listening(ctx, settings, handle_listen_error)

        self.assertEqual(other_client.disconnect_calls, [True])
        self.assertEqual(target_channel.connect_calls, [FakeVoiceRecvClient])
        self.assertEqual(len(target_channel.connected_client.listen_calls), 1)
        sink, after = target_channel.connected_client.listen_calls[0]
        self.assertIsInstance(sink, FakeSink)
        self.assertIs(sink.text_channel, ctx.channel)
        self.assertIsInstance(sink.recognizer, FakeRecognizerFactory)
        self.assertEqual(sink.recognizer.model_path, settings.vosk_model_path)
        self.assertIs(sink.loop, fake_loop)
        self.assertIs(after, handle_listen_error)
        self.assertEqual(
            ctx.sent_messages,
            [
                "VC `General` へ参加しました。 これからこのチャンネルで文字起こしを送ります。"
            ],
        )

    async def test_moves_existing_voice_client_and_restarts_recognition(self) -> None:
        settings = Settings("token", Path("model"), None)
        target_channel = FakeVoiceChannel("Meeting")
        current_channel = FakeVoiceChannel("Lobby")
        previous_sink = FakeManagedSink()
        voice_client = FakeVoiceRecvClient(
            channel=current_channel,
            listening=True,
            sink=previous_sink,
        )
        ctx = FakeContext(
            guild=FakeGuild(voice_client=voice_client),
            author=FakeMember(voice=SimpleNamespace(channel=target_channel)),
            channel=FakeTextChannel(),
        )

        with (
            patch("mituke.bot.commands.discord.Member", FakeMember),
            patch("mituke.bot.commands.VoiceRecvClient", FakeVoiceRecvClient),
            patch("mituke.bot.commands.TranscriptionSink", FakeSink),
            patch("mituke.bot.commands.VoskRecognizer", FakeRecognizerFactory),
            patch(
                "mituke.bot.commands.asyncio.get_running_loop", return_value=object()
            ),
        ):
            await start_listening(ctx, settings, Mock())

        self.assertTrue(voice_client.stop_called)
        self.assertTrue(previous_sink.request_stop_called)
        self.assertTrue(previous_sink.wait_closed_called)
        self.assertEqual(voice_client._ssrc_to_id, {})
        self.assertEqual(voice_client._id_to_ssrc, {})
        self.assertEqual(voice_client.move_calls, [target_channel])
        self.assertEqual(target_channel.connect_calls, [])
        self.assertEqual(len(voice_client.listen_calls), 1)
        self.assertEqual(
            ctx.sent_messages,
            [
                "VC `Meeting` へ参加しました。 これからこのチャンネルで文字起こしを送ります。"
            ],
        )

    async def test_clears_stale_source_mapping_before_starting(self) -> None:
        settings = Settings("token", Path("model"), None)
        target_channel = FakeVoiceChannel("Meeting")
        voice_client = FakeVoiceRecvClient(channel=target_channel, listening=False)
        ctx = FakeContext(
            guild=FakeGuild(voice_client=voice_client),
            author=FakeMember(voice=SimpleNamespace(channel=target_channel)),
            channel=FakeTextChannel(),
        )

        with (
            patch("mituke.bot.commands.discord.Member", FakeMember),
            patch("mituke.bot.commands.VoiceRecvClient", FakeVoiceRecvClient),
            patch("mituke.bot.commands.TranscriptionSink", FakeSink),
            patch("mituke.bot.commands.VoskRecognizer", FakeRecognizerFactory),
            patch(
                "mituke.bot.commands.asyncio.get_running_loop", return_value=object()
            ),
        ):
            await start_listening(ctx, settings, Mock())

        self.assertEqual(voice_client._ssrc_to_id, {})
        self.assertEqual(voice_client._id_to_ssrc, {})

    async def test_reports_model_load_failure_before_joining_voice(self) -> None:
        settings = Settings("token", Path("broken-model"), None)
        handle_listen_error = Mock()
        target_channel = FakeVoiceChannel("General")
        ctx = FakeContext(
            guild=FakeGuild(),
            author=FakeMember(voice=SimpleNamespace(channel=target_channel)),
            channel=FakeTextChannel(),
        )

        with (
            patch("mituke.bot.commands.discord.Member", FakeMember),
            patch(
                "mituke.bot.commands.VoskRecognizer",
                side_effect=RuntimeError("invalid model"),
            ),
        ):
            await start_listening(ctx, settings, handle_listen_error)

        handle_listen_error.assert_called_once()
        self.assertEqual(target_channel.connect_calls, [])
        self.assertEqual(
            ctx.sent_messages,
            [vosk_model_load_failed()],
        )


class StopListeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_server_context(self) -> None:
        ctx = FakeContext(guild=None, author=object(), channel=FakeTextChannel())

        await stop_listening(ctx)

        self.assertEqual(
            ctx.sent_messages,
            ["このコマンドはサーバー内で使ってください。"],
        )

    async def test_handles_missing_voice_client(self) -> None:
        ctx = FakeContext(
            guild=FakeGuild(voice_client=None),
            author=object(),
            channel=FakeTextChannel(),
        )

        await stop_listening(ctx)

        self.assertEqual(
            ctx.sent_messages,
            ["今はどのボイスチャンネルにも参加していません。"],
        )

    async def test_stops_recognition_and_disconnects(self) -> None:
        managed_sink = FakeManagedSink()
        voice_client = FakeVoiceRecvClient(listening=True, sink=managed_sink)
        ctx = FakeContext(
            guild=FakeGuild(voice_client=voice_client),
            author=object(),
            channel=FakeTextChannel(),
        )

        with patch("mituke.bot.commands.VoiceRecvClient", FakeVoiceRecvClient):
            await stop_listening(ctx)

        self.assertTrue(voice_client.stop_called)
        self.assertTrue(managed_sink.request_stop_called)
        self.assertTrue(managed_sink.wait_closed_called)
        self.assertEqual(voice_client._ssrc_to_id, {})
        self.assertEqual(voice_client._id_to_ssrc, {})
        self.assertEqual(voice_client.disconnect_calls, [True])
        self.assertEqual(ctx.sent_messages, ["ボイスチャンネルから退出しました。"])


class ShowHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_usage_guide(self) -> None:
        ctx = FakeContext(
            guild=FakeGuild(),
            author=object(),
            channel=FakeTextChannel(),
        )

        await show_help(ctx)

        self.assertEqual(len(ctx.sent_messages), 1)
        self.assertIn("`!join`", ctx.sent_messages[0])
        self.assertIn("`!leave`", ctx.sent_messages[0])
        self.assertIn("`!help`", ctx.sent_messages[0])

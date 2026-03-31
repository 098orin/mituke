from __future__ import annotations

SERVER_TEXT_CHANNEL_REQUIRED = (
    "このコマンドはサーバー内のテキストチャンネルで使ってください。"
)
SERVER_CONTEXT_REQUIRED = "このコマンドはサーバー内で使ってください。"
VOICE_CHANNEL_REQUIRED = "先に参加したいボイスチャンネルへ入ってください。"
VOICE_CLIENT_MISSING = "今はどのボイスチャンネルにも参加していません。"
VOICE_CHANNEL_LEFT = "ボイスチャンネルから退出しました。"
UNKNOWN_COMMAND = "不明なコマンドです。`!help` で使い方を確認できます。"
COMMAND_ERROR = "コマンドの実行中にエラーが発生しました。ログを確認してください。"
HELP_TEXT = (
    "使い方:\n"
    "`!join` で、あなたが入っている VC に Bot が参加します。\n"
    "`!leave` で、文字起こしを止めて VC から退出します。\n"
    "`!help` で、この案内をもう一度表示できます。"
)


def joined_voice_channel(channel_name: str) -> str:
    return (
        f"VC `{channel_name}` へ参加しました。"
        " これからこのチャンネルで文字起こしを送ります。"
    )


def voice_channel_emptied(channel_name: str) -> str:
    return f"VC {channel_name} が空になったため退出しました。"


def logged_in_as(user: object) -> str:
    return f"{user} としてログインしました。"


def listen_error(error: Exception) -> str:
    return f"音声処理でエラーが発生しました: {error}"


def vosk_model_load_failed() -> str:
    return "音声認識モデルを読み込めませんでした。ログを確認してください。"


def command_error(error: Exception) -> str:
    return f"コマンド実行中にエラーが発生しました: {error}"

from __future__ import annotations

from functools import lru_cache

from rich.console import Console
from vosk import Model

console = Console()


class VoskModelLoadError(RuntimeError):
    """Vosk モデルの初期化に失敗したことを表す。"""


@lru_cache(maxsize=4)
def load_vosk_model(model_path: str) -> Model:
    console.log(f"Vosk モデルを読み込みます: {model_path}")
    try:
        return Model(model_path)
    except Exception as error:
        raise VoskModelLoadError(
            f"Vosk モデルを読み込めませんでした: {model_path}"
        ) from error

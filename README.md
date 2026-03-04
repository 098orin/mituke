# mikute
VC での会話をテキスト化して、Discord に送る Discord bot

# 開発環境のセットアップ（uv 使用）

このプロジェクトでは **uv** を使用して依存関係と仮想環境を管理しています。

## 1. uv のインストール

公式サイトの手順に従ってインストールしてください。

```bash
uv --version
```



## 2. 仮想環境の作成

```bash
uv venv
```



## 3. 依存関係のインストール

```bash
uv sync
```



## 4. 仮想環境の有効化

**Windows (PowerShell)**

```powershell
.venv\Scripts\activate
```

**macOS / Linux**

```bash
source .venv/bin/activate
```



## 5. プロジェクトの実行

```bash
uv run python main.py
```



依存関係を追加する:

```bash
uv add パッケージ名
```

開発用依存関係を追加する:

```bash
uv add --dev パッケージ名
```

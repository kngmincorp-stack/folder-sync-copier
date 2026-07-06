# フォルダ同期コピー (Folder Sync Copier)

2 つの監視元フォルダを監視し、**新しくできたファイル**をそれぞれのコピー先フォルダへ自動でコピーする常駐ソフト（Windows）。

## 機能
- 監視元フォルダ参照 ×2、それぞれに対応するコピー先フォルダ参照
- 書き込み完了を待ってからコピー（サイズ安定を 2 回確認、途中ファイルをコピーしない）
- 同名同サイズはスキップ（重複・ループ防止）
- **Windows スタートアップ登録**チェックボックス（PC 起動時に自動実行＋自動監視開始）
- **パッチ更新システム**（GitHub Releases を参照して自動更新）
- 設定は `%APPDATA%\FolderSyncCopier\config.json` に保存

## 使い方
1. `FolderSyncCopier.exe` を起動
2. 「組 1」「組 2」に監視元フォルダとコピー先フォルダを指定
3. 「▶ 監視開始」を押す
4. 必要なら「Windows スタートアップに登録」にチェック

## 開発 / ビルド
- 実行: `python main.py`
- exe ビルド: `build.bat`（または `python -m PyInstaller --onefile --windowed --name FolderSyncCopier main.py`）
- 生成物: `dist\FolderSyncCopier.exe`（単一ファイル・依存ライブラリ同梱）

## パッチ更新の有効化
`version.py` の `GITHUB_OWNER` / `GITHUB_REPO` を配布用リポジトリに書き換え、
新バージョンは `version.py` の `__version__` を上げて GitHub Releases に `.exe` を添付するだけ。
アプリの「更新を確認」ボタンで新版を検出・ダウンロード・自己置換・再起動する。

## ファイル構成
| ファイル | 役割 |
|---|---|
| `main.py` | GUI（Tkinter）本体 |
| `watcher.py` | フォルダ監視・コピーエンジン（ポーリング方式） |
| `config.py` | 設定の保存/読み込み |
| `startup.py` | Windows スタートアップ登録/解除（レジストリ HKCU\Run） |
| `updater.py` | パッチ更新（GitHub Releases） |
| `version.py` | バージョン・更新先設定 |
| `build.bat` | exe ビルドスクリプト |

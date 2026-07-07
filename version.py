# -*- coding: utf-8 -*-
"""バージョン情報。パッチ更新システムはこの値をリモートと比較する。"""

__version__ = "1.1.2"
APP_NAME = "FolderSyncCopier"
APP_TITLE = "フォルダ同期コピー"

# パッチ更新の配布元。GitHub Releases の latest を参照する。
# 配布リポジトリを作ったら OWNER/REPO を書き換えるだけで自動更新が有効になる。
GITHUB_OWNER = "kngmincorp-stack"
GITHUB_REPO = "folder-sync-copier"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

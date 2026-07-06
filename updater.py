# -*- coding: utf-8 -*-
"""
パッチ更新システム。
GitHub Releases の latest を参照し、より新しいバージョンの .exe があれば
ダウンロードして自身を置き換える（再起動）。標準ライブラリのみで実装。
"""
import os
import sys
import json
import ssl
import tempfile
import subprocess
import urllib.request

from version import __version__, UPDATE_API_URL, APP_NAME


def _version_tuple(v: str):
    """'1.2.3' -> (1, 2, 3) 。比較用に数値化する。"""
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_latest(timeout=10):
    """
    リモートの最新リリースを取得。
    戻り値: dict(version, url, notes) または None（取得失敗）。
    """
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.load(resp)
    except Exception:
        return None

    tag = data.get("tag_name") or data.get("name") or ""
    exe_url = None
    for asset in data.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe"):
            exe_url = asset.get("browser_download_url")
            break
    if not tag or not exe_url:
        return None
    return {
        "version": tag.lstrip("vV"),
        "url": exe_url,
        "notes": data.get("body", "") or "",
    }


def is_newer(remote_version: str) -> bool:
    """リモート版が現行版より新しいか。"""
    return _version_tuple(remote_version) > _version_tuple(__version__)


def is_frozen() -> bool:
    """PyInstaller でビルドされた exe として動いているか。"""
    return getattr(sys, "frozen", False)


def download_and_apply(download_url: str, timeout=60):
    """
    新しい exe をダウンロードし、バッチ経由で自身を差し替えて再起動する。
    exe 実行時のみ有効（スクリプト実行時は False を返す）。
    """
    if not is_frozen():
        return False, "exe 実行時のみ自動更新できます（開発中はスキップ）。"

    current_exe = sys.executable  # 実行中の exe パス
    tmp_dir = tempfile.gettempdir()
    new_exe = os.path.join(tmp_dir, f"{APP_NAME}_new.exe")

    ctx = ssl.create_default_context()
    req = urllib.request.Request(download_url, headers={"User-Agent": APP_NAME})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, \
                open(new_exe, "wb") as f:
            f.write(resp.read())
    except Exception as e:
        return False, f"ダウンロード失敗: {e}"

    # 実行中の exe はロックされているため、終了を待ってから差し替える必要がある。
    # バッチで「本体終了待ち → 上書き → 再起動」を行う。
    bat = os.path.join(tmp_dir, f"{APP_NAME}_update.bat")
    bat_content = f"""@echo off
chcp 65001 >nul
echo アップデートを適用しています...
:waitloop
tasklist /FI "IMAGENAME eq {os.path.basename(current_exe)}" | find /I "{os.path.basename(current_exe)}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
move /Y "{new_exe}" "{current_exe}" >nul
start "" "{current_exe}"
del "%~f0"
"""
    try:
        with open(bat, "w", encoding="utf-8") as f:
            f.write(bat_content)
    except Exception as e:
        return False, f"更新スクリプト作成失敗: {e}"

    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    return True, "更新を適用します。アプリを再起動します。"

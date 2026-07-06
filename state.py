# -*- coding: utf-8 -*-
"""
コピー済み台帳（ledger）の永続化。
%APPDATA%\\FolderSyncCopier\\state.json に「どのファイルを既にコピーしたか」を保存し、
2 回目以降の起動/監視では済みファイルを避けて、新しく追加されたファイルだけコピーする。
"""
import os
import json

from version import APP_NAME


def _state_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


STATE_PATH = os.path.join(_state_dir(), "state.json")


def load() -> dict:
    """{ 組の署名: [コピー済みキー, ...] } を返す。"""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save(data: dict):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

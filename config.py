# -*- coding: utf-8 -*-
"""設定の保存/読み込み。%APPDATA%\\FolderSyncCopier\\config.json に保存する。"""
import os
import json

from version import APP_NAME


def _config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_PATH = os.path.join(_config_dir(), "config.json")

DEFAULT = {
    "pairs": [
        {"src": "", "dst": ""},
        {"src": "", "dst": ""},
    ],
    "startup": False,
    "interval": 2.0,
    # コピー対象の拡張子（小文字・ドット付き）。None にすると全ファイル対象。
    "extensions": [".txt"],
}


def load() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 欠損キーを補完
        merged = dict(DEFAULT)
        merged.update(data)
        # pairs を必ず 2 組に正規化
        pairs = merged.get("pairs") or []
        while len(pairs) < 2:
            pairs.append({"src": "", "dst": ""})
        merged["pairs"] = pairs[:2]
        return merged
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT))


def save(data: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

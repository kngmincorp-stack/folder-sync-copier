# -*- coding: utf-8 -*-
"""
Windows スタートアップ登録/解除。
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run にエントリを作る。
管理者権限不要（ユーザー単位）。
"""
import sys
import winreg

from version import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe_command() -> str:
    """スタートアップに登録する起動コマンド。--autostart 付きで起動する。"""
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        # 開発中(スクリプト実行)は pythonw + スクリプトパス
        exe = f'"{sys.executable}" "{__import__("os").path.abspath("main.py")}"'
        return f'{exe} --autostart'
    return f'"{exe}" --autostart'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _exe_command())


def disable():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def set_enabled(flag: bool):
    if flag:
        enable()
    else:
        disable()

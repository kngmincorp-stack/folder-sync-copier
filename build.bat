@echo off
chcp 65001 >nul
REM ============================================================
REM  フォルダ同期コピー  exe ビルドスクリプト
REM  実行すると dist\FolderSyncCopier.exe が生成されます。
REM ============================================================
cd /d "%~dp0"

echo [1/2] PyInstaller を確認しています...
python -m pip install --quiet --upgrade pyinstaller

echo [2/2] exe をビルドしています...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "FolderSyncCopier" ^
  --hidden-import tkinter ^
  main.py

echo.
echo 完了しました。
echo 配布用 exe: %~dp0dist\FolderSyncCopier.exe
pause

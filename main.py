# -*- coding: utf-8 -*-
"""
フォルダ同期コピー (Folder Sync Copier)
2 つの監視元フォルダを監視し、新しくできたファイルをそれぞれのコピー先へ自動コピーする。

・監視元フォルダ参照 ×2、各々にコピー先フォルダ参照を付属
・Windows スタートアップ登録チェックボックス
・パッチ更新システム（GitHub Releases 参照）
"""
import sys
import queue
import threading
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
import startup
import updater
from version import __version__, APP_TITLE, UPDATE_API_URL
from watcher import CopyPair, WatchEngine


class App(tk.Tk):
    def __init__(self, autostart=False):
        super().__init__()
        self.title(f"{APP_TITLE}  v{__version__}")
        self.geometry("720x560")
        self.minsize(640, 500)

        self.cfg = config.load()
        self.engine = WatchEngine(interval=self.cfg.get("interval", 1.0))
        self.engine.set_logger(self._log_threadsafe)
        self._log_queue = queue.Queue()

        self.src_vars = []
        self.dst_vars = []

        # スタートアップ自己修復:
        # 設定で有効なのに登録が消えている/古い exe を指している/
        # タスクマネージャーで無効化されている場合は、起動のたびに直す。
        self._startup_heal_msg = None
        if self.cfg.get("startup"):
            try:
                self._startup_heal_msg = startup.heal()
            except Exception as e:
                self._startup_heal_msg = f"自己修復に失敗しました: {e}"

        self.startup_var = tk.BooleanVar(value=startup.is_enabled())

        self._build_ui()
        self._load_into_ui()
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 起動時に版数と更新参照先をログ（別PCでどのビルドが動いているか確認用）
        self._log(f"起動: v{__version__}  更新参照先: {UPDATE_API_URL}")

        # スタートアップ登録の状態をログ（起動しない問題の切り分け用）
        if self._startup_heal_msg:
            self._log(f"[スタートアップ] {self._startup_heal_msg}")
        if self.startup_var.get():
            self._log(f"[スタートアップ] 登録コマンド: {startup.registered_command()}")
            if not startup.is_approved():
                self._log("[スタートアップ] 警告: タスクマネージャーで無効化されています。")

        # スタートアップ起動時は自動で監視開始
        if autostart:
            self.after(300, self._start)

    # ---------- UI 構築 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        header = ttk.Label(self, text="フォルダ同期コピー", font=("Meiryo UI", 15, "bold"))
        header.pack(anchor="w", padx=12, pady=(10, 2))
        exts = self.cfg.get("extensions")
        ext_label = "・".join(exts) if exts else "全ファイル"
        ttk.Label(
            self,
            text=f"監視元にできた新しいファイル（対象: {ext_label}）を、対応するコピー先へ同じ名前でコピーします。",
            foreground="#555",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        # 2 組のフォルダペア
        for i in range(2):
            frame = ttk.LabelFrame(self, text=f"　組 {i + 1} 　")
            frame.pack(fill="x", padx=12, pady=6)

            src_var = tk.StringVar()
            dst_var = tk.StringVar()
            self.src_vars.append(src_var)
            self.dst_vars.append(dst_var)

            # 監視元
            row1 = ttk.Frame(frame)
            row1.pack(fill="x", **pad)
            ttk.Label(row1, text="監視元フォルダ", width=14).pack(side="left")
            ttk.Entry(row1, textvariable=src_var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(row1, text="参照…", command=lambda v=src_var: self._browse(v)).pack(side="left")

            # コピー先
            row2 = ttk.Frame(frame)
            row2.pack(fill="x", **pad)
            ttk.Label(row2, text="コピー先フォルダ", width=14).pack(side="left")
            ttk.Entry(row2, textvariable=dst_var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(row2, text="参照…", command=lambda v=dst_var: self._browse(v)).pack(side="left")

        # オプション行
        opt = ttk.Frame(self)
        opt.pack(fill="x", padx=12, pady=(4, 2))
        ttk.Checkbutton(
            opt,
            text="Windows スタートアップに登録（PC 起動時に自動実行）",
            variable=self.startup_var,
            command=self._toggle_startup,
        ).pack(side="left")
        ttk.Button(opt, text="更新を確認", command=self._check_update).pack(side="right")
        ttk.Button(opt, text="台帳をリセット", command=self._reset_ledger).pack(side="right", padx=6)

        # 操作ボタン
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=6)
        self.start_btn = ttk.Button(btns, text="▶ 監視開始", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■ 停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.status_lbl = ttk.Label(btns, text="停止中", foreground="#c0392b")
        self.status_lbl.pack(side="left", padx=12)

        # ログ
        logframe = ttk.LabelFrame(self, text="　ログ　")
        logframe.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.log_text = tk.Text(logframe, height=10, wrap="none", state="disabled",
                                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        sb = ttk.Scrollbar(logframe, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sb.set)

    def _load_into_ui(self):
        pairs = self.cfg.get("pairs", [])
        for i in range(2):
            if i < len(pairs):
                self.src_vars[i].set(pairs[i].get("src", ""))
                self.dst_vars[i].set(pairs[i].get("dst", ""))

    # ---------- 動作 ----------
    def _browse(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or None)
        if d:
            var.set(d.replace("/", "\\"))

    def _collect_pairs(self):
        exts = self.cfg.get("extensions")  # 例: [".txt"] / None=全ファイル
        pairs = []
        for i in range(2):
            pairs.append(CopyPair(self.src_vars[i].get().strip(),
                                  self.dst_vars[i].get().strip(),
                                  extensions=exts))
        return pairs

    def _save_cfg(self):
        self.cfg["pairs"] = [
            {"src": self.src_vars[i].get().strip(), "dst": self.dst_vars[i].get().strip()}
            for i in range(2)
        ]
        self.cfg["startup"] = self.startup_var.get()
        config.save(self.cfg)

    def _start(self):
        pairs = self._collect_pairs()
        valid = [p for p in pairs if p.valid()]
        if not valid:
            messagebox.showwarning("設定不足",
                                   "有効な監視元フォルダとコピー先フォルダを\n少なくとも 1 組指定してください。")
            return
        # 監視元が存在しない組を警告
        for idx, p in enumerate(pairs, 1):
            if (p.src or p.dst) and not p.valid():
                self._log(f"[警告] 組 {idx} は監視元フォルダが見つからないためスキップします。")

        self._save_cfg()
        self.engine.set_pairs(pairs)
        self.engine.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_lbl.config(text="● 監視中", foreground="#27ae60")
        self._set_entries_state("disabled")

    def _stop(self):
        self.engine.stop()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="停止中", foreground="#c0392b")
        self._set_entries_state("normal")

    def _set_entries_state(self, state):
        # 監視中は入力を触れないようにする（表示のみ簡易ロック）
        pass

    def _reset_ledger(self):
        if not messagebox.askyesno(
                "台帳をリセット（確認）",
                "本当にコピー済みの記録（台帳）を全て消去しますか？\n\n"
                "【注意】コピー先に無い対象ファイルは、すべて『新規』とみなされ\n"
                "再びコピーされます。コピー先が自動で削除される運用では、\n"
                "既にコピー済みの大量ファイルが再コピーされる場合があります。\n\n"
                "通常は押す必要はありません。\n"
                "「はい」で消去、「いいえ」で中止します。",
                icon="warning", default="no"):
            self._log("台帳リセットは中止しました。")
            return
        # 監視中でない場合でも state.json を確実に空にするため、
        # 現在の設定で一度 pairs を engine に読み込ませてからリセットする。
        if not self.engine.running:
            self.engine.set_pairs(self._collect_pairs())
        self.engine.reset_ledger()
        self._log("台帳をリセットしました。コピー先に無い対象ファイルは再コピーされます。")

    def _toggle_startup(self):
        try:
            startup.set_enabled(self.startup_var.get())
            self._save_cfg()
            if self.startup_var.get():
                self._log("Windows スタートアップに登録しました。")
            else:
                self._log("Windows スタートアップ登録を解除しました。")
        except Exception as e:
            messagebox.showerror("エラー", f"スタートアップ設定に失敗しました:\n{e}")
            self.startup_var.set(startup.is_enabled())

    # ---------- 更新 ----------
    def _check_update(self):
        self._log("更新を確認しています…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        info = updater.check_latest()
        if not info:
            reason = updater.LAST_ERROR or "不明なエラー"
            self._log_threadsafe(f"[更新] 情報を取得できませんでした: {reason}")
            self._log_threadsafe(f"[更新] 参照先: {UPDATE_API_URL}")
            self.after(0, lambda: messagebox.showwarning(
                "更新確認", f"更新情報を取得できませんでした。\n\n理由: {reason}"))
            return
        if not updater.is_newer(info["version"]):
            self._log_threadsafe(f"[更新] 最新版です（現行 v{__version__}）。")
            self.after(0, lambda: messagebox.showinfo("更新確認",
                       f"お使いのバージョンは最新です。\n現行: v{__version__}"))
            return
        self.after(0, lambda: self._prompt_update(info))

    def _prompt_update(self, info):
        msg = (f"新しいバージョン v{info['version']} が見つかりました。\n"
               f"現行: v{__version__}\n\n更新しますか？（更新後に再起動します）")
        if not messagebox.askyesno("更新があります", msg):
            return
        ok, detail = updater.download_and_apply(info["url"])
        self._log(f"[更新] {detail}")
        if ok:
            self.after(500, self._quit_for_update)
        else:
            messagebox.showwarning("更新", detail)

    def _quit_for_update(self):
        self.engine.stop()
        self.destroy()
        sys.exit(0)

    # ---------- ログ ----------
    def _log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{ts}  {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_threadsafe(self, msg):
        self._log_queue.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.after(150, self._drain_log)

    # ---------- 終了 ----------
    def _on_close(self):
        self._save_cfg()
        self.engine.stop()
        self.destroy()


def _selftest_update():
    """更新参照の自己診断。結果を %TEMP%\\fsc_selftest.txt に書き出して終了。
    （--windowed の exe はコンソール出力が無いためファイルに書く。）"""
    import os
    import tempfile
    import updater
    path = os.path.join(tempfile.gettempdir(), "fsc_selftest.txt")
    try:
        import certifi
        ca = certifi.where()
        ca_info = f"{ca} (exists={os.path.isfile(ca)}, frozen={getattr(sys, 'frozen', False)})"
    except Exception as e:
        ca_info = f"certifi 読込失敗: {e}"
    try:
        import watcher
        wd = f"HAS_WATCHDOG={watcher._HAS_WATCHDOG}"
    except Exception as e:
        wd = f"watcher読込失敗: {e}"
    info = updater.check_latest()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"version={__version__}\n")
        f.write(f"api={UPDATE_API_URL}\n")
        f.write(f"certifi={ca_info}\n")
        f.write(f"watchdog={wd}\n")
        if info:
            f.write(f"result=OK remote={info['version']}\n")
        else:
            f.write(f"result=FAIL error={updater.LAST_ERROR}\n")


def _selftest_apply():
    """更新適用（ダウンロード→自己置換→再起動）の自己診断。
    最新リリースを取得して download_and_apply を実行し、結果をファイルに残して終了する。"""
    import os
    import tempfile
    import updater
    path = os.path.join(tempfile.gettempdir(), "fsc_apply_selftest.txt")
    info = updater.check_latest()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"exe={sys.executable}\n")
        if not info:
            f.write(f"result=FAIL check error={updater.LAST_ERROR}\n")
            return
        ok, detail = updater.download_and_apply(info["url"])
        f.write(f"apply_ok={ok} detail={detail}\n")


def _selftest_realtime():
    """フリーズ済みexeでリアルタイムコピーが機能するかを自己診断。
    一時フォルダで監視を起動→ファイル作成→コピー遅延を計測し結果を書き出す。"""
    import os
    import time
    import tempfile
    import shutil
    import watcher
    out = os.path.join(tempfile.gettempdir(), "fsc_realtime_selftest.txt")
    src = tempfile.mkdtemp()
    dst = tempfile.mkdtemp()
    try:
        eng = watcher.WatchEngine()
        eng.set_logger(lambda m: None)
        eng.set_pairs([watcher.CopyPair(src, dst, extensions=[".txt"])])
        eng.start()
        time.sleep(1.5)
        t0 = time.monotonic()
        with open(os.path.join(src, "rt.txt"), "w") as f:
            f.write("realtime-check")
        lat = None
        for _ in range(150):
            if os.path.isfile(os.path.join(dst, "rt.txt")):
                lat = time.monotonic() - t0
                break
            time.sleep(0.02)
        eng.stop()
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"has_watchdog={watcher._HAS_WATCHDOG}\n")
            f.write(f"latency_sec={round(lat, 3) if lat is not None else 'TIMEOUT'}\n")
            f.write(f"copied={os.path.isfile(os.path.join(dst, 'rt.txt'))}\n")
    finally:
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)


def main():
    if "--selftest-update" in sys.argv:
        _selftest_update()
        return
    if "--selftest-realtime" in sys.argv:
        _selftest_realtime()
        return
    if "--selftest-apply" in sys.argv:
        _selftest_apply()
        return
    autostart = "--autostart" in sys.argv
    app = App(autostart=autostart)
    app.mainloop()


if __name__ == "__main__":
    main()

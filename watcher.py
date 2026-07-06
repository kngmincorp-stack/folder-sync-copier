# -*- coding: utf-8 -*-
"""
フォルダ監視 & コピーエンジン（ポーリング方式・依存ライブラリなし）。

・監視元フォルダを一定間隔でスキャン。
・新しく現れたファイルを、サイズが安定してから（＝書き込み完了後に）コピー先へコピー。
・書き込み途中の不完全ファイルをコピーしないよう、2 回連続で同サイズを確認する。
・コピー済みファイルは「台帳(ledger)」に記録し state.json に永続化。
  → 2 回目以降の起動/監視では済みファイルを避け、新しく追加されたファイルだけコピーする。
  （コピー先からファイルを移動/削除しても、済みファイルを再コピーしない。）
"""
import os
import shutil
import threading

import state


class CopyPair:
    """監視元→コピー先 の 1 組。"""

    def __init__(self, src, dst, ledger=None, extensions=None):
        self.src = src
        self.dst = dst
        self._sizes = {}                       # ファイル名 -> 前回スキャン時のサイズ（安定判定用）
        self.ledger = set(ledger) if ledger else set()  # コピー済みキーの集合（永続化対象）
        self.dirty = False                     # 台帳に変化があったか（保存要否）
        # コピー対象拡張子（小文字・ドット付きの集合）。None なら全ファイル対象。
        self.extensions = None
        if extensions:
            self.extensions = {e.lower() if e.startswith(".") else "." + e.lower()
                               for e in extensions}
        self._announced = False                # 初回スキャンの検出件数ログ済みフラグ

    def _match_ext(self, name) -> bool:
        if self.extensions is None:
            return True
        return os.path.splitext(name)[1].lower() in self.extensions

    def valid(self) -> bool:
        return bool(self.src) and bool(self.dst) and os.path.isdir(self.src)

    def sig(self) -> str:
        """組の署名（台帳の保存キー）。監視元・コピー先の組み合わせで一意。"""
        return f"{os.path.normcase(self.src)}=>{os.path.normcase(self.dst)}"

    @staticmethod
    def _safe_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return -1

    @staticmethod
    def _is_locked(path) -> bool:
        """別プロセスが書き込み中（ロック中）かの簡易判定。
        書き込み用に開ければロックされていない＝書き込み完了とみなせる。
        開けない場合はロック中、または読み取り専用（呼び出し側でサイズ安定を保険に使う）。"""
        try:
            with open(path, "rb+"):
                return False
        except OSError:
            return True

    @staticmethod
    def _file_key(name, size, mtime) -> str:
        """ファイルの同一性キー。名前+サイズ+更新時刻。
        同名でも内容が変われば別キーになり『新しく追加されたファイル』として扱う。"""
        return f"{name}|{size}|{int(mtime)}"

    def prime_existing(self, log=lambda m: None):
        """初回登録時、監視元に既にあるファイルを『コピー対象』として次スキャンに委ねる。
        （台帳が空＝初回はここでは何もせず、通常スキャンで全件コピーされる。）"""
        return

    def scan_once(self, log, should_stop=None, persist_cb=None):
        if not self.valid():
            return
        try:
            os.makedirs(self.dst, exist_ok=True)
        except OSError as e:
            log(f"[エラー] コピー先を作成できません {self.dst}: {e}")
            return

        try:
            entries = os.listdir(self.src)
        except OSError as e:
            log(f"[エラー] 監視元を読めません {self.src}: {e}")
            return

        # 初回スキャン時、対象ファイルの内訳をログ（なぜコピーされる/されないかを可視化）
        # 実際のコピー判定と同じ基準（台帳＋コピー先の存在）で分類する。
        if not self._announced:
            self._announced = True
            ext_label = "/".join(sorted(self.extensions)) if self.extensions else "全ファイル"
            targets = [n for n in entries
                       if os.path.isfile(os.path.join(self.src, n)) and self._match_ext(n)]
            n_ledger = n_exist = n_copy = 0
            for n in targets:
                sp = os.path.join(self.src, n)
                dp = os.path.join(self.dst, n)
                try:
                    size = os.path.getsize(sp)
                    mtime = os.path.getmtime(sp)
                except OSError:
                    continue
                if self._file_key(n, size, mtime) in self.ledger:
                    n_ledger += 1            # 台帳に「コピー済み」記録あり → スキップ
                elif os.path.isfile(dp) and self._safe_size(dp) == size:
                    n_exist += 1             # コピー先に同名同サイズ既存 → スキップ
                else:
                    n_copy += 1              # 実際にコピーされる
            log(f"[監視元] {self.src}")
            log(f"  対象({ext_label}) {len(targets)} 件 ／ 台帳済み {n_ledger} 件 ／ "
                f"コピー先に既存 {n_exist} 件 ／ 今回コピー {n_copy} 件")
            if targets and n_copy == 0 and n_ledger > 0:
                log("  ※ 対象は過去にコピー済み（台帳記録）のためスキップします。"
                    "コピー先から消した物を再コピーしたい場合は［台帳をリセット］を押してください。")

        current = set()
        copied = 0        # このスキャンで実際にコピーした件数
        errors = 0
        aborted = False
        for idx, name in enumerate(entries):
            # 大量コピー中でも［停止］が効くように、時々中断を確認する
            if should_stop and (idx & 0x1FF) == 0 and should_stop():
                aborted = True
                break

            src_path = os.path.join(self.src, name)
            if not os.path.isfile(src_path):
                continue  # サブフォルダは対象外
            if not self._match_ext(name):
                continue  # 対象拡張子以外はスキップ（例: .txt のみ）
            current.add(name)
            try:
                size = os.path.getsize(src_path)
                mtime = os.path.getmtime(src_path)
            except OSError:
                continue

            prev = self._sizes.get(name)
            self._sizes[name] = size

            # 書き込み完了の判定（安全側）。両方を満たしたときだけコピーする:
            #   1) サイズが前回スキャンから変化していない（＝もう書き足されていない）
            #   2) ファイルがロックされていない（＝書き込みハンドルが閉じられている）
            # 部分ファイルのコピーを防ぐため、どちらか一方でも欠ければ次回に回す。
            stable = (prev is not None and prev == size)
            if not stable or self._is_locked(src_path):
                continue

            key = self._file_key(name, size, mtime)

            # 既にコピー済み（過去の起動を含む）ならスキップ
            if key in self.ledger:
                continue

            dst_path = os.path.join(self.dst, name)
            # コピー先に同名同サイズが既にある場合は、コピーせず済み扱いにする
            # （初回にコピー先へ既存ファイルがあるケースの無駄コピー防止）
            if os.path.isfile(dst_path):
                try:
                    if os.path.getsize(dst_path) == size:
                        self.ledger.add(key)
                        self.dirty = True
                        continue
                except OSError:
                    pass

            try:
                shutil.copy2(src_path, dst_path)
                self.ledger.add(key)
                self.dirty = True
                copied += 1
                # ログの洪水を防ぐ: 最初の 20 件はファイル名を表示、
                # それ以降は 1000 件ごとに進捗のみ表示する。
                if copied <= 20:
                    log(f"[コピー] {name}  →  {self.dst}")
                elif copied % 1000 == 0:
                    log(f"  …コピー中 {copied} 件")
                # 大量コピーを中断に強くするため、途中で台帳を保存する
                if persist_cb and copied % 2000 == 0:
                    persist_cb()
            except OSError as e:
                errors += 1
                if errors <= 20:
                    log(f"[エラー] コピー失敗 {name}: {e}")

        # 消えたファイルのサイズ記録を掃除（台帳は保持し続ける）
        for gone in list(self._sizes.keys()):
            if gone not in current:
                self._sizes.pop(gone, None)

        # コピーが発生したスキャンの最後に「完了通知」を出す
        if copied > 0:
            if aborted:
                log(f"[中断] {self.src} → {self.dst} : {copied} 件コピーして停止しました。"
                    f"残りは次回の監視開始時にコピーされます。")
            else:
                tail = f"（うち失敗 {errors} 件）" if errors else ""
                log(f"[完了] {self.src} → {self.dst} : {copied} 件コピーしました{tail}。")
        if errors and copied == 0:
            log(f"[エラー] {self.src}: コピーに {errors} 件失敗しました。")


class WatchEngine:
    """複数の CopyPair をバックグラウンドスレッドでポーリングする。台帳は state.json に永続化。"""

    def __init__(self, interval=2.0):
        self.interval = interval
        self.pairs = []
        self._thread = None
        self._stop = threading.Event()
        self._log_cb = lambda msg: None
        self._state = state.load()

    def set_pairs(self, pairs):
        """有効な組だけ採用し、永続化された台帳を各組に読み込む。"""
        self.pairs = []
        for p in pairs:
            if not p.valid():
                continue
            saved = self._state.get(p.sig(), [])
            p.ledger = set(saved)
            p.dirty = False
            self.pairs.append(p)

    def set_logger(self, cb):
        self._log_cb = cb

    def reset_ledger(self):
        """コピー済み台帳を全消去し、state.json も空にする。
        以後の監視で、コピー先に無い対象ファイルは再びコピーされる。"""
        self._state = {}
        for p in self.pairs:
            p.ledger = set()
            p._sizes = {}
            p._announced = False
            p.dirty = False
        state.save(self._state)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _persist(self):
        """台帳に変化があれば state.json に保存。"""
        changed = False
        for p in self.pairs:
            if p.dirty:
                self._state[p.sig()] = sorted(p.ledger)
                p.dirty = False
                changed = True
        if changed:
            state.save(self._state)

    def _run(self):
        n = sum(1 for _ in self.pairs)
        self._log_cb(f"監視を開始しました（{n} 組）。既にコピー済みのファイルはスキップします。")
        while not self._stop.is_set():
            for pair in self.pairs:
                try:
                    pair.scan_once(self._log_cb,
                                   should_stop=self._stop.is_set,
                                   persist_cb=self._persist)
                except Exception as e:  # スレッドを絶対に落とさない
                    self._log_cb(f"[例外] {e}")
            self._persist()
            self._stop.wait(self.interval)
        self._persist()
        self._log_cb("監視を停止しました。")

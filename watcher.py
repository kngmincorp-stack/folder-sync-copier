# -*- coding: utf-8 -*-
"""
フォルダ監視 & コピーエンジン（リアルタイム版）。

・watchdog（OS のファイル変更通知 / Windows は ReadDirectoryChangesW）で
  新規ファイルを即座に検知し、書き込み完了（ロック解除）を待って即コピーする。
  → 監視元フォルダに大量のファイルがあっても、全件を舐めずに新規だけ拾える＝リアルタイム。
・保険として低頻度・高速（os.scandir + 台帳照合）の全体スキャンも回し、
  通知が届かなかったファイルを取りこぼさないようにする（ネットワークドライブ対策）。
・コピー済みファイルは「台帳(ledger)」に記録し state.json に永続化。
  → 済みファイルは避け、新規だけコピー。コピー元/先が消えても再コピーしない。
"""
import os
import time
import shutil
import threading

import state

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAS_WATCHDOG = True
except Exception:  # 取り込み失敗時はスキャンのみで動作
    Observer = None
    FileSystemEventHandler = object
    _HAS_WATCHDOG = False


class CopyPair:
    """監視元→コピー先 の 1 組。"""

    def __init__(self, src, dst, ledger=None, extensions=None):
        self.src = src
        self.dst = dst
        self._sizes = {}                       # 互換用（reset_ledger で参照）
        self.ledger = set(ledger) if ledger else set()  # コピー済みキーの集合（永続化対象）
        self.dirty = False                     # 台帳に変化があったか（保存要否）
        self.extensions = None                 # コピー対象拡張子（小文字・ドット付き集合）
        if extensions:
            self.extensions = {e.lower() if e.startswith(".") else "." + e.lower()
                               for e in extensions}
        self._announced = False

    # ---------- 判定ヘルパ ----------
    def valid(self) -> bool:
        return bool(self.src) and bool(self.dst) and os.path.isdir(self.src)

    def sig(self) -> str:
        return f"{os.path.normcase(self.src)}=>{os.path.normcase(self.dst)}"

    def _match_ext(self, name) -> bool:
        if self.extensions is None:
            return True
        return os.path.splitext(name)[1].lower() in self.extensions

    @staticmethod
    def _safe_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return -1

    @staticmethod
    def _file_key(name, size, mtime) -> str:
        """ファイルの同一性キー。名前+サイズ+更新時刻。
        同名でも内容が変われば別キーになり『新しく追加されたファイル』として扱う。"""
        return f"{name}|{size}|{int(mtime)}"

    @staticmethod
    def _is_locked(path) -> bool:
        """別プロセスが書き込み中（ロック中）かの簡易判定。
        書き込み用に開ければロックされていない＝書き込み完了とみなせる。"""
        try:
            with open(path, "rb+"):
                return False
        except OSError:
            return True

    # ---------- コピー本体 ----------
    def try_copy(self, name, log, size=None, mtime=None):
        """1 ファイルをコピー判定＆コピー。
        戻り値: 'copied' / 'skip' / 'locked'（書き込み中→呼び出し側でリトライ） / 'error'"""
        if not self._match_ext(name):
            return 'skip'
        src_path = os.path.join(self.src, name)
        if size is None or mtime is None:
            try:
                if not os.path.isfile(src_path):
                    return 'skip'
                size = os.path.getsize(src_path)
                mtime = os.path.getmtime(src_path)
            except OSError:
                return 'skip'

        key = self._file_key(name, size, mtime)
        if key in self.ledger:
            return 'skip'                       # 既にコピー済み

        # まだ書き込み中ならリトライへ（不完全コピー防止）
        if self._is_locked(src_path):
            return 'locked'

        dst_path = os.path.join(self.dst, name)
        # コピー先に同名同サイズが既にあれば済み扱い（無駄コピー防止）
        if os.path.isfile(dst_path):
            try:
                if os.path.getsize(dst_path) == size:
                    self.ledger.add(key)
                    self.dirty = True
                    return 'skip'
            except OSError:
                pass

        try:
            os.makedirs(self.dst, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            self.ledger.add(key)
            self.dirty = True
            return 'copied'
        except OSError as e:
            log(f"[エラー] コピー失敗 {name}: {e}")
            return 'error'

    # ---------- 全体スキャン（保険 / 初回バックログ） ----------
    def scan(self, log, should_stop=None, persist_cb=None, announce=False):
        """os.scandir で監視元を一巡し、未コピーの対象をコピーする。
        scandir の stat キャッシュを使うため、大量ファイルでもネットワーク往復を抑えられる。"""
        if not self.valid():
            return
        try:
            os.makedirs(self.dst, exist_ok=True)
        except OSError as e:
            log(f"[エラー] コピー先を作成できません {self.dst}: {e}")
            return

        n_target = n_ledger = n_exist = n_copy = copied = errors = 0
        aborted = False
        try:
            scanner = os.scandir(self.src)
        except OSError as e:
            log(f"[エラー] 監視元を読めません {self.src}: {e}")
            return

        with scanner:
            for idx, entry in enumerate(scanner):
                if should_stop and (idx & 0x3FF) == 0 and should_stop():
                    aborted = True
                    break
                name = entry.name
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if not self._match_ext(name):
                    continue
                try:
                    st = entry.stat()          # scandir キャッシュ（追加の往復なし）
                    size, mtime = st.st_size, st.st_mtime
                except OSError:
                    continue

                n_target += 1
                key = self._file_key(name, size, mtime)
                if key in self.ledger:
                    n_ledger += 1
                    continue                    # 済み → 何もしない（高速）

                r = self.try_copy(name, log, size=size, mtime=mtime)
                if r == 'copied':
                    copied += 1
                    n_copy += 1
                    if copied <= 20:
                        log(f"[コピー] {name}  →  {self.dst}")
                    elif copied % 1000 == 0:
                        log(f"  …コピー中 {copied} 件")
                    if persist_cb and copied % 2000 == 0:
                        persist_cb()
                elif r == 'skip':
                    n_exist += 1                # コピー先に既存など
                elif r == 'error':
                    errors += 1

        if announce and not self._announced:
            self._announced = True
            ext_label = "/".join(sorted(self.extensions)) if self.extensions else "全ファイル"
            log(f"[監視元] {self.src}")
            log(f"  対象({ext_label}) {n_target} 件 ／ 台帳済み {n_ledger} 件 ／ "
                f"コピー先に既存など {n_exist} 件 ／ 今回コピー {n_copy} 件")

        if copied > 0:
            if aborted:
                log(f"[中断] {self.src} → {self.dst} : {copied} 件コピーして停止しました。")
            else:
                tail = f"（うち失敗 {errors} 件）" if errors else ""
                log(f"[完了] {self.src} → {self.dst} : {copied} 件コピーしました{tail}。")


class _PairHandler(FileSystemEventHandler):
    """watchdog イベント → エンジンのキューへ投入。"""

    def __init__(self, engine, pair):
        self._engine = engine
        self._pair = pair

    def _submit(self, path, is_dir):
        if is_dir:
            return
        self._engine._enqueue(self._pair, os.path.basename(path))

    def on_created(self, event):
        self._submit(event.src_path, event.is_directory)

    def on_modified(self, event):
        self._submit(event.src_path, event.is_directory)

    def on_moved(self, event):
        # 一時名 → 本名 のリネームで完成するアプリに対応
        self._submit(getattr(event, "dest_path", event.src_path), event.is_directory)


class WatchEngine:
    """watchdog によるリアルタイム検知 ＋ 低頻度の保険スキャン。台帳は state.json に永続化。"""

    SAFETY_SCAN_INTERVAL = 15.0    # 取りこぼし対策の全体スキャン間隔（秒）
    WORKER_TICK = 0.2              # キュー処理の間隔（秒）＝ロック解除後の追従速度

    def __init__(self, interval=1.0):
        self.pairs = []
        self._log_cb = lambda msg: None
        self._state = state.load()
        self._stop = threading.Event()
        self._worker = None
        self._observers = []
        self._pending = {}                       # (sig, name) -> (pair, name)  重複除去
        self._pending_lock = threading.Lock()

    # ---------- 設定 ----------
    def set_pairs(self, pairs):
        self.pairs = []
        for p in pairs:
            if not p.valid():
                continue
            p.ledger = set(self._state.get(p.sig(), []))
            p.dirty = False
            p._announced = False
            self.pairs.append(p)

    def set_logger(self, cb):
        self._log_cb = cb

    def reset_ledger(self):
        self._state = {}
        for p in self.pairs:
            p.ledger = set()
            p._sizes = {}
            p._announced = False
            p.dirty = False
        state.save(self._state)

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # ---------- キュー ----------
    def _enqueue(self, pair, name):
        with self._pending_lock:
            self._pending[(pair.sig(), name)] = (pair, name)

    def _drain(self):
        with self._pending_lock:
            batch = list(self._pending.values())
            self._pending = {}
        return batch

    # ---------- 永続化 ----------
    def _persist(self):
        changed = False
        for p in self.pairs:
            if p.dirty:
                self._state[p.sig()] = sorted(p.ledger)
                p.dirty = False
                changed = True
        if changed:
            state.save(self._state)

    # ---------- 起動/停止 ----------
    def start(self):
        if self.running:
            return
        self._stop.clear()
        # リアルタイム検知（watchdog）を各監視元に仕掛ける
        self._observers = []
        rt = 0
        if _HAS_WATCHDOG:
            for p in self.pairs:
                try:
                    obs = Observer()
                    obs.schedule(_PairHandler(self, p), p.src, recursive=False)
                    obs.start()
                    self._observers.append(obs)
                    rt += 1
                except Exception as e:
                    self._log_cb(f"[注意] {p.src} のリアルタイム監視を開始できません: {e}")
        self._worker = threading.Thread(target=self._run, args=(rt,), daemon=True)
        self._worker.start()

    def stop(self):
        self._stop.set()
        for obs in self._observers:
            try:
                obs.stop()
            except Exception:
                pass
        for obs in self._observers:
            try:
                obs.join(timeout=2)
            except Exception:
                pass
        self._observers = []

    # ---------- ワーカー ----------
    def _run(self, realtime_count):
        n = len(self.pairs)
        mode = "リアルタイム監視" if realtime_count == n and n > 0 else \
               ("一部リアルタイム＋定期スキャン" if realtime_count else "定期スキャン")
        self._log_cb(f"監視を開始しました（{n} 組・{mode}）。既存のコピー済みファイルはスキップします。")

        # 初回: 監視開始前から在るファイル / 未コピー分を拾う（scandir で高速）
        for p in self.pairs:
            try:
                p.scan(self._log_cb, should_stop=self._stop.is_set,
                       persist_cb=self._persist, announce=True)
            except Exception as e:
                self._log_cb(f"[例外] 初回スキャン {p.src}: {e}")
        self._persist()

        last_safety = time.monotonic()
        while not self._stop.is_set():
            # リアルタイム: 通知で溜まった新規ファイルを処理
            batch = self._drain()
            copied = 0
            for pair, name in batch:
                try:
                    r = pair.try_copy(name, self._log_cb)
                except Exception as e:
                    self._log_cb(f"[例外] {name}: {e}")
                    continue
                if r == 'locked':
                    self._enqueue(pair, name)        # まだ書き込み中 → 次tickで再試行
                elif r == 'copied':
                    copied += 1
                    self._log_cb(f"[コピー] {name}  →  {pair.dst}")
            if copied:
                self._persist()

            # 保険: 低頻度の全体スキャン（通知が届かない環境でも取りこぼさない）
            if time.monotonic() - last_safety >= self.SAFETY_SCAN_INTERVAL:
                for p in self.pairs:
                    try:
                        p.scan(self._log_cb, should_stop=self._stop.is_set,
                               persist_cb=self._persist)
                    except Exception as e:
                        self._log_cb(f"[例外] 定期スキャン {p.src}: {e}")
                self._persist()
                last_safety = time.monotonic()

            self._stop.wait(self.WORKER_TICK)

        self._persist()
        self._log_cb("監視を停止しました。")

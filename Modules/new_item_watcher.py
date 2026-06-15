#Real-time new-item detection via Plex's notifications websocket.
#Listens to the Plex server's `/:/websockets/notifications` endpoint (through plexapi's AlertListener) 

import os
import subprocess
import sys
import threading
import time

import yaml

MTDP_DEBUG = os.environ.get("MTDP_DEBUG", "").strip().lower() in ("1", "true", "yes")

# Plex metadata type ints we trigger on: 1=movie, 2=show, 3=season and 4=episode
_WANTED_TYPES = (1, 2)


class NewItemWatcher:
    COOLDOWN_SECONDS = 900        # ignore repeat events for an item for 15 min after handling
    MAX_PENDING = 50             # circuit breaker: defer to the scheduled scan during a storm
    MAX_DEFER_SECONDS = 1800     # burst coalescing cap: handle at most 30 min after first event
    BACKOFF_START = 5
    BACKOFF_MAX = 300
    SETTLE_SECONDS = 1.0         # grace period to confirm a freshly-started listener is alive
    POLL_SECONDS = 5.0

    def __init__(self, config_path, sched_state, movies_script, tv_script):
        self._config_path = config_path
        self._sched_state = sched_state
        self._movies_script = movies_script
        self._tv_script = tv_script

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._wake = threading.Event()           # wakes the supervisor (apply_config/shutdown)
        self._shutdown = threading.Event()
        self._item_run_active = threading.Event()  # set while a single-item subprocess runs

        self._started = False
        self._enabled = False
        self._delay = 60
        self._config_dirty = False
        self._connected = False
        self._error = ""
        self._last_event_ts = None
        self._last_processed = None

        self._pending = {}        # ratingKey -> {"due": epoch, "first_seen": epoch}
        self._cooldown = {}       # ratingKey -> expiry epoch
        self._section_map = {}    # str(sectionID) -> "movie" | "tv"
        self._storm_warned = False

        self._listener = None
        self._current_proc = None
        self._supervisor_thread = None
        self._worker_thread = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Spawn the supervisor + worker threads (idempotent).

        Probes for websocket-client first; if it is missing we record an error
        and do not spawn the connect loop (avoids a hot reconnect loop).
        """
        with self._lock:
            if self._started:
                return
            self._started = True

        self.apply_config()  # load enabled/delay from config.yml

        try:
            import websocket  # noqa: F401  (plexapi's AlertListener needs this)
        except ImportError:
            with self._lock:
                self._error = "websocket-client not installed"
            print("[Watcher] websocket-client is not installed — new-item detection disabled.")
            return

        self._supervisor_thread = threading.Thread(
            target=self._supervise, daemon=True, name="new-item-watcher")
        self._worker_thread = threading.Thread(
            target=self._work, daemon=True, name="new-item-worker")
        self._supervisor_thread.start()
        self._worker_thread.start()

    def shutdown(self):
        """Stop all threads, the listener, and any in-flight subprocess."""
        self._shutdown.set()
        self._wake.set()
        with self._lock:
            self._cond.notify_all()
            proc = self._current_proc
        self._stop_listener()
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    def apply_config(self):
        """Re-read config.yml and apply enabled/delay; force a reconnect.

        Called after the Settings page saves so changes (including Plex
        URL/token/library edits) take effect without a container restart.
        """
        cfg = self._read_config()
        enabled = bool(cfg.get("NEW_ITEM_DETECTION", False))
        try:
            delay = int(cfg.get("NEW_ITEM_DELAY", 60))
        except (TypeError, ValueError):
            delay = 60
        if delay < 0:
            delay = 0
        with self._lock:
            self._enabled = enabled
            self._delay = delay
            self._config_dirty = True  # rebuild listener + section map on next supervisor pass
            if not enabled:
                self._pending.clear()
                self._storm_warned = False
            self._cond.notify_all()
        self._wake.set()

    def wait_for_item_run(self, timeout=600):
        """Block until any in-flight single-item subprocess finishes.

        Called by the scheduler before a full run (after it sets status to
        'running') so a full scan can't race an in-flight single-item download.
        """
        deadline = time.monotonic() + timeout
        while self._item_run_active.is_set():
            if time.monotonic() >= deadline:
                print("[Watcher] Timed out waiting for an in-flight item check; proceeding with the scheduled run.")
                return
            time.sleep(0.2)

    def get_status_dict(self):
        with self._lock:
            return {
                "enabled": self._enabled,
                "connected": self._connected,
                "last_event": self._last_event_ts,
                "pending": len(self._pending),
                "last_processed": self._last_processed,
                "error": self._error,
            }

    # ── Supervisor: owns the AlertListener ────────────────────────────────

    def _supervise(self):
        backoff = self.BACKOFF_START
        listener_started_at = 0.0
        while not self._shutdown.is_set():
            with self._lock:
                enabled = self._enabled
                dirty = self._config_dirty
                self._config_dirty = False

            if dirty:
                self._stop_listener()

            if not enabled:
                self._set_connected(False)
                self._stop_listener()
                self._wake.wait(self.POLL_SECONDS)
                self._wake.clear()
                continue

            listener = self._listener
            if listener is not None and listener.is_alive():
                self._set_connected(True)
                if time.monotonic() - listener_started_at >= 60:
                    backoff = self.BACKOFF_START
                self._wake.wait(self.POLL_SECONDS)
                self._wake.clear()
                continue

            # (Re)connect: the listener is dead or was never created.
            self._set_connected(False)
            self._stop_listener()
            try:
                self._connect()
                listener_started_at = time.monotonic()
                print("[Watcher] Connected to Plex notifications websocket — watching for new items.")
            except Exception as e:
                with self._lock:
                    self._error = f"connect failed: {e}"
                print(f"[Watcher] Could not connect to Plex notifications ({e}). Retrying in {backoff}s.")
                self._wake.wait(backoff)
                self._wake.clear()
                backoff = min(backoff * 2, self.BACKOFF_MAX)

        self._stop_listener()

    def _connect(self):
        """Build a Plex connection, the section map, and start a live listener."""
        from plexapi.alert import AlertListener

        plex = self._build_plex()
        section_map = self._build_section_map(plex)
        if not section_map:
            raise RuntimeError("no configured Plex libraries found on the server")
        # Publish the section map before the listener starts so no early event
        # is dropped for want of a map.
        with self._lock:
            self._section_map = section_map

        listener = AlertListener(plex, self._on_alert, self._on_alert_error)
        listener.start()
        # AlertListener.run() imports websocket and connects; on failure the
        # thread exits within a moment, so confirm it's actually alive.
        self._shutdown.wait(self.SETTLE_SECONDS)
        if not listener.is_alive():
            try:
                listener.stop()
            except Exception:
                pass
            raise RuntimeError("listener exited immediately (websocket connect failed)")

        with self._lock:
            self._listener = listener
            self._error = ""

    def _stop_listener(self):
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            # AlertListener.stop() raises AttributeError if the ws never opened.
            try:
                listener.stop()
            except Exception:
                pass

    def _set_connected(self, value):
        with self._lock:
            self._connected = value

    # ── Alert callback (runs in the AlertListener thread; must stay cheap) ──

    def _on_alert(self, data):
        try:
            if data.get("type") != "timeline":
                return
            entries = data.get("TimelineEntry") or []
            now = time.time()
            with self._lock:
                self._last_event_ts = now
                if not self._enabled:
                    return
                delay = self._delay
                section_map = self._section_map
                for entry in entries:
                    try:
                        if entry.get("identifier") != "com.plexapp.plugins.library":
                            continue
                        if int(entry.get("state", -1)) != 0:           # 0 = item created
                            continue
                        if int(entry.get("type", -1)) not in _WANTED_TYPES:
                            continue
                        if str(entry.get("sectionID")) not in section_map:
                            continue
                        rk = int(entry.get("itemID"))
                    except (TypeError, ValueError):
                        continue

                    cd = self._cooldown.get(rk)
                    if cd is not None and now < cd:
                        continue

                    if MTDP_DEBUG:
                        print(f"[Watcher] new-item event: ratingKey={rk} type={entry.get('type')} "
                              f"sectionID={entry.get('sectionID')} title={entry.get('title')}")

                    existing = self._pending.get(rk)
                    if existing is None:
                        if len(self._pending) >= self.MAX_PENDING:
                            if not self._storm_warned:
                                print(f"[Watcher] More than {self.MAX_PENDING} new items at once — "
                                      f"deferring the rest to the next scheduled scan.")
                                self._storm_warned = True
                            continue
                        self._pending[rk] = {"due": now + delay, "first_seen": now}
                    else:
                        # Repeat event: push the due time out so bursts coalesce,
                        # capped relative to when we first saw the item.
                        existing["due"] = min(now + delay, existing["first_seen"] + self.MAX_DEFER_SECONDS)
                self._cond.notify()
        except Exception as e:
            if MTDP_DEBUG:
                print(f"[Watcher] alert handler error: {e}")

    def _on_alert_error(self, error):
        # The listener thread will exit after an error; the supervisor reconnects.
        if MTDP_DEBUG:
            print(f"[Watcher] websocket error: {error}")

    # ── Worker: drains the pending queue serially ─────────────────────────

    def _work(self):
        while not self._shutdown.is_set():
            rk = self._wait_for_due_item()
            if rk is None or self._shutdown.is_set():
                continue
            with self._lock:
                enabled = self._enabled
            if not enabled:
                continue
            try:
                self._process(rk)
            except Exception as e:
                print(f"[Watcher] Error handling ratingKey {rk}: {e}")

    def _wait_for_due_item(self):
        """Block until a pending item is due, then pop and return its ratingKey."""
        with self._lock:
            while not self._shutdown.is_set():
                self._expire_cooldowns()
                now = time.time()
                due_rk = None
                earliest = None
                for rk, info in self._pending.items():
                    if info["due"] <= now and (earliest is None or info["due"] < earliest):
                        earliest = info["due"]
                        due_rk = rk
                if due_rk is not None:
                    del self._pending[due_rk]
                    if not self._pending:
                        self._storm_warned = False
                    return due_rk
                if self._pending:
                    next_due = min(info["due"] for info in self._pending.values())
                    timeout = max(0.1, min(self.POLL_SECONDS, next_due - now))
                else:
                    timeout = self.POLL_SECONDS
                self._cond.wait(timeout)
        return None

    def _process(self, raw_rk):
        plex = self._get_worker_plex()
        if plex is None:
            print(f"[Watcher] Cannot check ratingKey {raw_rk}: no Plex connection.")
            return

        try:
            item = plex.fetchItem(raw_rk)
        except Exception:
            if MTDP_DEBUG:
                print(f"[Watcher] ratingKey {raw_rk} not found (deleted before check?) — skipping.")
            return

        item_type = getattr(item, "type", "")
        if item_type == "movie":
            target, script, kind = item, self._movies_script, "movie"
        elif item_type == "show":
            target, script, kind = item, self._tv_script, "show"
        else:
            return

        try:
            target_rk = int(target.ratingKey)
        except (TypeError, ValueError, AttributeError):
            return

        # Only act on items in a configured library.
        lib_title = getattr(target, "librarySectionTitle", "")
        movie_names, tv_names = self._configured_library_names(self._read_config())
        if kind == "movie" and lib_title not in movie_names:
            return
        if kind == "show" and lib_title not in tv_names:
            return

        # Skip if we just handled this item (dedupe repeat created-events).
        with self._lock:
            cd = self._cooldown.get(target_rk)
            if cd is not None and time.time() < cd:
                return

        cached = self._get_cached_item(target_rk)
        if cached is not None:
            status = cached.get("trailerStatus")
            if status in ("local", "plexpass") or cached.get("genreSkipped"):
                if MTDP_DEBUG:
                    print(f"[Watcher] '{getattr(target, 'title', target_rk)}' already has a trailer "
                          f"or is genre-skipped — skipping.")
                self._add_cooldown(target_rk)
                self._add_cooldown(raw_rk)
                return

        claimed = False
        while not self._shutdown.is_set():
            with self._lock:
                if not self._enabled:
                    return
            self._item_run_active.set()
            if self._sched_state is None or self._sched_state.status != "running":
                claimed = True
                break
            self._item_run_active.clear()
            self._shutdown.wait(15)
        if not claimed or self._shutdown.is_set():
            self._item_run_active.clear()
            return

        # Cooldown both keys now (pre-run) to absorb any event echoes, then run.
        self._add_cooldown(raw_rk)
        self._add_cooldown(target_rk)
        title = getattr(target, "title", str(target_rk))
        print(f"[Watcher] Checking '{title}' (ratingKey {target_rk}) for a missing trailer...")
        try:
            self._run_item_subprocess(script, target_rk)
        finally:
            self._item_run_active.clear()

        self._upsert_cache_item(target_rk)
        self._refresh_library_cache()
        with self._lock:
            self._last_processed = {"ratingKey": target_rk, "title": title, "at": time.time()}

    def _run_item_subprocess(self, script, rating_key):
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", script, "--rating-key", str(rating_key)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                errors="replace",
            )
            with self._lock:
                self._current_proc = proc
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
            proc.wait()
        except Exception as e:
            print(f"[Watcher] Item check subprocess failed: {e}")
        finally:
            with self._lock:
                self._current_proc = None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _read_config(self):
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _build_plex(self):
        from plexapi.server import PlexServer

        cfg = self._read_config()
        url = cfg.get("PLEX_URL")
        token = cfg.get("PLEX_TOKEN")
        if not url or not token or token == "YOUR_PLEX_TOKEN":
            raise RuntimeError("Plex credentials not configured")
        try:
            timeout = int(cfg.get("PLEX_TIMEOUT", 120))
        except (TypeError, ValueError):
            timeout = 120
        if timeout < 30:
            timeout = 120
        return PlexServer(url, token, timeout=timeout)

    def _get_worker_plex(self):
        try:
            return self._build_plex()
        except Exception:
            return None

    def _configured_library_names(self, cfg):
        """Return (movie_names, tv_names) sets, honoring the legacy single-name keys."""
        def _names(list_key, single_key):
            names = [(l.get("name") if isinstance(l, dict) else l) for l in (cfg.get(list_key) or [])]
            if not names and cfg.get(single_key):
                names = [cfg.get(single_key)]
            return set(n for n in names if n)
        return _names("MOVIE_LIBRARIES", "MOVIE_LIBRARY_NAME"), _names("TV_LIBRARIES", "TV_LIBRARY_NAME")

    def _build_section_map(self, plex):
        cfg = self._read_config()
        movie_names, tv_names = self._configured_library_names(cfg)
        section_map = {}
        for section in plex.library.sections():
            key = str(section.key)
            if section.title in movie_names:
                section_map[key] = "movie"
            elif section.title in tv_names:
                section_map[key] = "tv"
        return section_map

    def _add_cooldown(self, rk):
        with self._lock:
            self._cooldown[int(rk)] = time.time() + self.COOLDOWN_SECONDS

    def _expire_cooldowns(self):
        """Drop expired cooldown entries. Caller must hold self._lock."""
        now = time.time()
        for k in [k for k, t in self._cooldown.items() if t <= now]:
            del self._cooldown[k]

    def _get_cached_item(self, rating_key):
        try:
            from webui.routes import get_cached_item
            return get_cached_item(rating_key)
        except Exception:
            return None

    def _upsert_cache_item(self, rating_key):
        try:
            from webui.routes import upsert_cache_item
            upsert_cache_item(rating_key)
        except Exception:
            pass

    def _refresh_library_cache(self):
        """Trigger a full background cache rebuild (stats recompute + orphan cleanup)."""
        try:
            from webui.routes import refresh_library_cache
            refresh_library_cache()
        except Exception:
            pass

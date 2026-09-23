"""Track downloaded trailers in a JSON file for the dashboard carousel and statistics."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path


def _trailer_owner_folder(file_path):
    """Return the media folder a trailer file belongs to (parent of a 'Trailers' dir)."""
    folder = os.path.dirname(os.path.normpath(file_path))
    if os.path.basename(folder).lower() == 'trailers':
        folder = os.path.dirname(folder)
    return folder


class TrailerTracker:
    """Manages a JSON file that tracks all local trailer files."""

    def __init__(self, tracker_path: str = None):
        if tracker_path is None:
            if os.environ.get('IS_DOCKER', 'false').lower() == 'true':
                tracker_path = '/config/trailers.json'
            else:
                tracker_path = os.path.join(os.path.dirname(__file__), 'config', 'trailers.json')
        self._path = tracker_path
        self._lock = threading.Lock()
        self._data = {"trailers": []}
        self._load()

    def _load(self):
        """Load tracker data from disk."""
        try:
            if os.path.exists(self._path):
                with open(self._path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                if "trailers" not in self._data:
                    self._data = {"trailers": []}
        except Exception:
            self._data = {"trailers": []}
        self._data.setdefault("upgrade_attempts", {})

    def _save(self):
        """Save tracker data to disk atomically."""
        tmp_path = self._path + '.tmp'
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def reload(self):
        """Reload tracker data from disk (for use when external processes may have updated the file)."""
        with self._lock:
            self._load()

    def add_trailer(self, file_path: str, title: str, year: str = "",
                    media_type: str = "movie", plex_rating_key: str = "",
                    poster_url: str = "", thumb_url: str = ""):
        """Add a newly downloaded trailer to the tracker."""
        with self._lock:
            self._load()
            # Remove existing entries for same path OR same Plex item (rating key).
            # This prevents duplicates when the same movie gets a new trailer
            # (e.g. after changing preferred language).
            self._data["trailers"] = [
                t for t in self._data["trailers"]
                if t.get("file_path") != file_path
                and not (plex_rating_key and t.get("plex_rating_key") == plex_rating_key)
            ]
            self._data["trailers"].append({
                "file_path": file_path,
                "title": title,
                "year": str(year),
                "media_type": media_type,
                "plex_rating_key": plex_rating_key,
                "poster_url": poster_url,
                "thumb_url": thumb_url,
                "downloaded_at": datetime.now().isoformat(),
            })
            self._save()

    def mark_upgrade_attempt(self, rating_key, attempted_min):
        """Record that a trailer-upgrade attempt found no higher-res source."""
        if not rating_key:
            return
        with self._lock:
            self._load()
            self._data["upgrade_attempts"][str(rating_key)] = {
                "attempted_min": int(attempted_min),
                "attempted_at": datetime.now().isoformat(),
            }
            self._save()

    def was_upgrade_attempted(self, rating_key, min_res):
        """Return True if an upgrade was already attempted at or above min_res."""
        if not rating_key:
            return False
        with self._lock:
            self._load()
            record = self._data.get("upgrade_attempts", {}).get(str(rating_key))
            return bool(record) and int(record.get("attempted_min", 0)) >= int(min_res)

    def get_upgrade_attempt(self, rating_key):
        """Return the recorded upgrade-attempt record for this rating key, or None.

        Record shape: {"attempted_min": int, "attempted_at": iso-string}.
        """
        if not rating_key:
            return None
        with self._lock:
            self._load()
            return self._data.get("upgrade_attempts", {}).get(str(rating_key))

    def clear_upgrade_attempts(self):
        """Forget all recorded upgrade attempts so the next run retries them.

        Returns the number of entries removed.
        """
        with self._lock:
            self._load()
            count = len(self._data.get("upgrade_attempts", {}))
            self._data["upgrade_attempts"] = {}
            self._save()
            return count

    def remove_missing(self):
        """Remove entries whose files no longer exist on disk."""
        with self._lock:
            self._load()
            before = len(self._data["trailers"])
            self._data["trailers"] = [
                t for t in self._data["trailers"] if os.path.exists(t.get("file_path", ""))
            ]
            removed = before - len(self._data["trailers"])
            if removed > 0:
                self._save()
            return removed

    def get_recent(self, limit: int = 30):
        """Get the most recently downloaded trailers, deduplicated per Plex item."""
        with self._lock:
            sorted_trailers = sorted(
                self._data["trailers"],
                key=lambda t: t.get("downloaded_at", ""),
                reverse=True
            )
            # Keep only the most recent entry per plex_rating_key to avoid
            # duplicates (e.g. after language change + re-download).
            seen_keys = set()
            unique = []
            for t in sorted_trailers:
                rk = t.get("plex_rating_key", "")
                if rk and rk in seen_keys:
                    continue
                if rk:
                    seen_keys.add(rk)
                unique.append(t)
            return unique[:limit]

    def get_all(self):
        """Get all tracked trailers."""
        with self._lock:
            return list(self._data["trailers"])

    def count(self):
        """Get total number of tracked trailers."""
        with self._lock:
            return len(self._data["trailers"])

    def sync_with_library(self, items):
        """Reconcile tracked trailers against the current Plex library.

        items: one dict per Plex item in the configured libraries -
        {"plex_rating_key", "title", "year", "media_type", "trailer_file", "folder"}
        where trailer_file is the item's local trailer ("" if none) and folder is
        its media folder. The list must describe the *complete* library: entries
        whose item can't be found in it are dropped.

        - Entries whose file is gone are dropped.
        - Entries whose rating key is still in Plex are kept (title/year refreshed).
        - Entries whose rating key is gone (item deleted, or re-matched under a new
          key) are relinked to the item owning their file/folder, else dropped.
        - Local trailers not tracked yet are indexed, dated by file mtime.
        - Upgrade-attempt records for items no longer in Plex are pruned.

        Returns {"removed": n, "relinked": n, "added": n}.
        """
        by_key = {}
        file_owners = {}
        folder_owners = {}
        for item in items:
            rk = str(item.get("plex_rating_key") or "")
            if not rk:
                continue
            by_key[rk] = item
            if item.get("trailer_file"):
                file_owners.setdefault(os.path.normpath(item["trailer_file"]), set()).add(rk)
            if item.get("folder"):
                folder_owners.setdefault(os.path.normpath(item["folder"]), set()).add(rk)
        # A file or folder claimed by several items (e.g. a flat movie folder with
        # a shared Trailers dir) can't identify its owner, so it is left out.
        by_file = {p: by_key[next(iter(o))] for p, o in file_owners.items() if len(o) == 1}
        by_folder = {p: by_key[next(iter(o))] for p, o in folder_owners.items() if len(o) == 1}

        def _apply_plex_fields(entry, item):
            updates = {
                "title": item.get("title") or entry.get("title", ""),
                "year": str(item.get("year") or entry.get("year", "")),
                "media_type": item.get("media_type") or entry.get("media_type", ""),
            }
            changed = any(entry.get(k) != v for k, v in updates.items())
            entry.update(updates)
            return changed

        with self._lock:
            self._load()
            removed = relinked = added = 0
            changed = False
            kept = []
            for t in self._data["trailers"]:
                path = t.get("file_path", "")
                if not path or not os.path.exists(path):
                    removed += 1
                    continue
                item = by_key.get(str(t.get("plex_rating_key") or ""))
                if item is None:
                    norm = os.path.normpath(path)
                    item = by_file.get(norm) or by_folder.get(_trailer_owner_folder(norm))
                    if item is None:
                        removed += 1
                        continue
                    rk = str(item["plex_rating_key"])
                    t["plex_rating_key"] = rk
                    t["poster_url"] = f"/api/plex/poster/{rk}"
                    relinked += 1
                    changed = True
                if _apply_plex_fields(t, item):
                    changed = True
                kept.append(t)

            tracked_keys = {str(t.get("plex_rating_key") or "") for t in kept}
            tracked_files = {os.path.normpath(t["file_path"]) for t in kept}
            for norm, item in by_file.items():
                rk = str(item["plex_rating_key"])
                if rk in tracked_keys or norm in tracked_files:
                    continue
                try:
                    mtime = os.path.getmtime(item["trailer_file"])
                except OSError:
                    continue
                kept.append({
                    "file_path": item["trailer_file"],
                    "title": item.get("title", ""),
                    "year": str(item.get("year") or ""),
                    "media_type": item.get("media_type", "movie"),
                    "plex_rating_key": rk,
                    "poster_url": f"/api/plex/poster/{rk}",
                    "thumb_url": "",
                    "downloaded_at": datetime.fromtimestamp(mtime).isoformat(),
                })
                tracked_keys.add(rk)
                added += 1

            attempts = self._data.get("upgrade_attempts", {})
            stale_attempts = [k for k in attempts if k not in by_key]
            for k in stale_attempts:
                del attempts[k]

            self._data["trailers"] = kept
            if changed or removed or added or stale_attempts:
                self._save()
            return {"removed": removed, "relinked": relinked, "added": added}

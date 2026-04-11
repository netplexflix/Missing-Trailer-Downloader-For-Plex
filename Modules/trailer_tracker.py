"""Track downloaded trailers in a JSON file for the dashboard carousel and statistics."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path


class TrailerTracker:
    """Manages a JSON file that tracks all local trailer files."""

    VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}

    def __init__(self, tracker_path: str = None):
        if tracker_path is None:
            if os.environ.get('IS_DOCKER', 'false').lower() == 'true':
                tracker_path = '/config/trailers.json'
            else:
                tracker_path = os.path.join(os.path.dirname(__file__), 'config', 'trailers.json')
        self._path = tracker_path
        self._lock = threading.Lock()
        self._data = {"trailers": []}
        self.scan_progress = {"scanning": False, "directory": "", "found": 0}
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

    def scan_directories(self, directories: list):
        """Scan directories for existing '-trailer' files and index them.

        This is used on first run when the JSON doesn't exist yet.
        """
        found = 0
        self.scan_progress = {"scanning": True, "directory": "", "found": 0}
        try:
            with self._lock:
                self._load()
                existing_paths = {t["file_path"] for t in self._data["trailers"]}

                for directory in directories:
                    if not os.path.isdir(directory):
                        continue
                    self.scan_progress["directory"] = os.path.basename(directory.rstrip('/\\')) or directory
                    for root, dirs, files in os.walk(directory):
                        for filename in files:
                            filepath = os.path.join(root, filename)
                            name, ext = os.path.splitext(filename)
                            if ext.lower() not in self.VIDEO_EXTENSIONS:
                                continue
                            if not name.lower().endswith('-trailer'):
                                continue
                            if filepath in existing_paths:
                                continue

                            # Extract title from filename: "Movie Name (2024)-trailer.mkv"
                            base = name[:-len('-trailer')]
                            title = base
                            year = ""
                            if base.endswith(')') and '(' in base:
                                idx = base.rfind('(')
                                possible_year = base[idx+1:-1].strip()
                                if possible_year.isdigit() and len(possible_year) == 4:
                                    year = possible_year
                                    title = base[:idx].strip()

                            # Detect media type from path
                            media_type = "movie"
                            path_lower = filepath.lower().replace('\\', '/')
                            if '/tv' in path_lower or '/series' in path_lower or '/shows' in path_lower:
                                media_type = "tvshow"

                            self._data["trailers"].append({
                                "file_path": filepath,
                                "title": title,
                                "year": year,
                                "media_type": media_type,
                                "plex_rating_key": "",
                                "poster_url": "",
                                "thumb_url": "",
                                "downloaded_at": datetime.fromtimestamp(
                                    os.path.getmtime(filepath)
                                ).isoformat(),
                            })
                            existing_paths.add(filepath)
                            found += 1
                            self.scan_progress["found"] = found

                if found > 0:
                    self._save()
        finally:
            self.scan_progress = {"scanning": False, "directory": "", "found": 0}
        return found

    def needs_initial_scan(self):
        """Check if we need to do an initial directory scan."""
        if not os.path.exists(self._path):
            return True
        # Also scan if the file exists but has no entries (e.g. first run created empty file)
        with self._lock:
            return len(self._data.get("trailers", [])) == 0

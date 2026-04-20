"""Flask routes and config metadata for MTDP Web UI."""

import os
import re
import json
import subprocess
import sys
import threading
import yaml
import requests
import time
from datetime import datetime
from flask import render_template, jsonify, request, Response

import webui

IS_DOCKER = os.environ.get('IS_DOCKER', 'false').lower() == 'true'
MTDP_DEBUG = os.environ.get('MTDP_DEBUG', 'false').lower() == 'true'


def _normalize_path(path):
    """Normalize paths for Docker compatibility (mirrors Movies.py logic).

    Unix paths (starting with /) are returned as-is.
    Windows paths are converted: D:\\Movies\\... → /D/Movies/...
    """
    if not IS_DOCKER:
        return path
    if not path or path.startswith('/'):
        return path
    drive_match = re.match(r'^([A-Za-z]):', path)
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        path_without_drive = path[2:]
        path_normalized = path_without_drive.replace('\\', '/')
        return f'/{drive_letter}{path_normalized}'
    return path.replace('\\', '/')


# ── Library / stats cache ─────────────────────────────────────────────────
_cache_lock = threading.Lock()
_cache_data = {
    "stats": None,
    "movies": None,
    "tvshows": None,
    "last_refreshed": None,
}
_cache_refreshing = False
_cache_refresh_pending = False
_cache_progress = {
    "refreshing": False,
    "phase": "",
    "current_library": "",
    "processed": 0,
    "total": 0,
}

_known_trailer_paths = set()


def _get_cache_path():
    """Return the path to the cache JSON file."""
    if IS_DOCKER:
        return '/config/library_cache.json'
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'library_cache.json')


def _load_cache():
    """Load cache from disk into memory."""
    global _cache_data
    path = _get_cache_path()
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with _cache_lock:
                _cache_data = data
                _rebuild_known_trailer_paths()
            # Pre-populate allowed-dirs cache from loaded data so the first
            # trailer stream doesn't block on a PlexServer connection.
            _prepopulate_allowed_dirs(data)
            # Pre-warm trailer files in the background so they're ready to
            # play without delay from cold storage.
            threading.Thread(target=_prewarm_trailer_files, args=("boot",),
                             daemon=True, name="prewarm-boot").start()
    except Exception:
        pass


def _prepopulate_allowed_dirs(cache_data):
    """Extract media directories from cached items to warm the allowed-dirs cache.

    This avoids a cold PlexServer connection when the first trailer is played.
    """
    if _allowed_dirs_cache["dirs"]:
        return  # Already populated
    dirs = []
    if IS_DOCKER:
        for candidate in ['/media', '/data', '/mnt']:
            if os.path.isdir(candidate):
                dirs.append(os.path.realpath(candidate))
    for collection in ['movies', 'tvshows']:
        items = cache_data.get(collection) or []
        for item in items:
            for key in ('trailerFile', 'mediaPath'):
                p = item.get(key, '')
                if not p:
                    continue
                # Go up to the library root (2 levels up from file, 1 from show folder)
                d = os.path.dirname(p) if os.path.splitext(p)[1] else p
                parent = os.path.realpath(os.path.dirname(d))
                if parent and parent not in dirs:
                    dirs.append(parent)
    if dirs:
        _allowed_dirs_cache["dirs"] = dirs
        _allowed_dirs_cache["timestamp"] = time.time()


def _save_cache():
    """Save in-memory cache to disk."""
    path = _get_cache_path()
    tmp = path + '.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _cache_lock:
            snapshot = json.loads(json.dumps(_cache_data))
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _rebuild_known_trailer_paths():
    """Rebuild the set of valid trailer paths from _cache_data.

    Caller must hold _cache_lock. Stores both the raw stored path and its
    os.path.normpath() form so look-ups tolerate trivial separator differences.
    """
    global _known_trailer_paths
    paths = set()
    for collection in ('movies', 'tvshows'):
        for item in (_cache_data.get(collection) or []):
            tf = item.get('trailerFile') or ''
            if tf:
                paths.add(tf)
                paths.add(os.path.normpath(tf))
    _known_trailer_paths = paths


def _update_cache_item_status(rating_key, new_status, trailer_file=""):
    """Optimistically update a single item's trailer status in the cache.

    Called immediately after a manual download so the UI reflects the change
    without waiting for the full background cache refresh.
    """
    rating_key = int(rating_key)
    resolution = _get_trailer_resolution(trailer_file) if new_status == "local" and trailer_file else ""
    language = _detect_trailer_language(trailer_file) if new_status == "local" and trailer_file else ""
    with _cache_lock:
        if _cache_data.get("movies") is None and _cache_data.get("tvshows") is None:
            return

        old_status = None
        is_movie = False

        # Search in movies
        if _cache_data.get("movies"):
            for item in _cache_data["movies"]:
                if item.get("ratingKey") == rating_key:
                    old_status = item.get("trailerStatus")
                    item["trailerStatus"] = new_status
                    item["trailerFile"] = trailer_file
                    item["trailerResolution"] = resolution
                    item["trailerLanguage"] = language
                    is_movie = True
                    break

        # Search in tvshows if not found in movies
        if old_status is None and _cache_data.get("tvshows"):
            for item in _cache_data["tvshows"]:
                if item.get("ratingKey") == rating_key:
                    old_status = item.get("trailerStatus")
                    item["trailerStatus"] = new_status
                    item["trailerFile"] = trailer_file
                    item["trailerResolution"] = resolution
                    item["trailerLanguage"] = language
                    break

        # Update stats if status actually changed
        if old_status and old_status != new_status and _cache_data.get("stats"):
            stats = _cache_data["stats"]
            if old_status == "missing":
                key = "movies_missing_trailers" if is_movie else "shows_missing_trailers"
                stats[key] = max(0, stats.get(key, 0) - 1)
            elif old_status == "local":
                local_key = "movies_local_trailers" if is_movie else "shows_local_trailers"
                stats[local_key] = max(0, stats.get(local_key, 0) - 1)

            if new_status == "local":
                local_key = "movies_local_trailers" if is_movie else "shows_local_trailers"
                stats[local_key] = stats.get(local_key, 0) + 1
            elif new_status == "missing":
                key = "movies_missing_trailers" if is_movie else "shows_missing_trailers"
                stats[key] = stats.get(key, 0) + 1

        _rebuild_known_trailer_paths()

    _save_cache()


def _classify_resolution(width, height):
    """Classify resolution using both width and height.

    For cinematic aspect ratios (e.g. 1280x550) the height alone
    under-reports the quality.  We derive an effective height from the
    width assuming 16:9 and take the higher of the two values.
    """
    effective_height = max(height, int(width * 9 / 16))
    for threshold, label in [(2160, "2160p"), (1440, "1440p"), (1080, "1080p"),
                             (720, "720p"), (480, "480p"), (360, "360p")]:
        if effective_height >= threshold:
            return label
    return f"{height}p"


def _get_trailer_resolution(trailer_file):
    """Get the resolution label (e.g. '1080p') of a local trailer file using ffprobe."""
    if not trailer_file or not os.path.isfile(trailer_file):
        return ""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', trailer_file],
            capture_output=True, text=True, timeout=10
        )
        dims = result.stdout.strip().split(',')
        if len(dims) == 2:
            width, height = int(dims[0]), int(dims[1])
            return _classify_resolution(width, height)
        # Fallback: single value means only height was returned
        height = int(dims[0])
        return _classify_resolution(0, height)
    except Exception:
        return ""


def _check_local_trailer_movie(movie):
    """Check if a movie has a local trailer file. Returns (has_local, trailer_file)."""
    try:
        for media in movie.media:
            for part in media.parts:
                media_dir = os.path.dirname(_normalize_path(part.file))
                trailers_dir = os.path.join(media_dir, 'Trailers')
                if os.path.isdir(trailers_dir):
                    for f in os.listdir(trailers_dir):
                        if '-trailer' in f.lower():
                            return True, os.path.join(trailers_dir, f)
                basename = os.path.splitext(os.path.basename(part.file))[0]
                for ext in ['.mkv', '.mp4', '.webm', '.avi', '.mov']:
                    candidate = os.path.join(media_dir, basename + '-trailer' + ext)
                    if os.path.exists(candidate):
                        return True, candidate
    except Exception:
        pass
    return False, ""


def _get_show_folder(show, section_locations):
    """Resolve the actual on-disk folder for a TV show.

    Prefers show.locations (direct from Plex) which works even when the
    folder name doesn't match the Plex title (e.g. localised titles).
    Falls back to constructing the path from the title.
    """
    # Try Plex-reported locations first (most reliable)
    try:
        for loc in show.locations:
            norm = _normalize_path(loc)
            if os.path.isdir(norm):
                return norm
    except Exception:
        pass
    # Fallback: construct from section locations + title
    for loc in section_locations:
        loc = _normalize_path(loc)
        show_folder = os.path.join(loc, show.title)
        if not os.path.isdir(show_folder):
            show_folder = os.path.join(loc, f"{show.title} ({show.year})")
        if os.path.isdir(show_folder):
            return show_folder
    return ""


def _check_local_trailer_show(show, section_locations):
    """Check if a TV show has a local trailer file. Returns (has_local, trailer_file)."""
    try:
        show_folder = _get_show_folder(show, section_locations)
        if show_folder:
            trailers_dir = os.path.join(show_folder, 'Trailers')
            if os.path.isdir(trailers_dir):
                for f in os.listdir(trailers_dir):
                    if '-trailer' in f.lower():
                        return True, os.path.join(trailers_dir, f)
            # Check for -trailer file in show root
            for f in os.listdir(show_folder):
                if '-trailer' in f.lower() and os.path.isfile(os.path.join(show_folder, f)):
                    name_part, ext = os.path.splitext(f)
                    if ext.lower() in {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.webm', '.m4v'}:
                        return True, os.path.join(show_folder, f)
    except Exception:
        pass
    return False, ""


def _check_plexpass_trailer(item):
    """Check if a Plex item has Plex Pass trailers.

    Uses the same detection as Movies.py: type='clip' and subtype='trailer'.
    Note: This returns True for ANY trailer Plex knows about (including local
    ones Plex has indexed). The caller must check local files first and give
    local priority via _determine_trailer_status().

    Returns (has_plexpass, extra_rating_key) where extra_rating_key is the
    ratingKey of the first trailer extra (used for streaming).
    """
    try:
        for extra in item.extras():
            if extra.type == 'clip' and extra.subtype == 'trailer':
                return True, str(extra.ratingKey)
    except Exception:
        pass
    return False, ""


def _determine_trailer_status(has_local, local_file, has_plexpass, check_plex_pass):
    """Determine trailer status. Local takes priority over Plex Pass."""
    if has_local:
        return "local", local_file
    if check_plex_pass and has_plexpass:
        return "plexpass", ""
    return "missing", ""


def _prewarm_trailer_files(trigger="unknown"):
    """Read head + tail of each local trailer to warm the OS filesystem cache."""
    try:
        with _cache_lock:
            movies = list(_cache_data.get("movies") or [])
            tvshows = list(_cache_data.get("tvshows") or [])
        if MTDP_DEBUG:
            print(f"[DIAG] prewarm START trigger={trigger} files={len(movies) + len(tvshows)}")
        count = 0
        slow = []  # (filename, ms) for files >= 500 ms
        t_start = time.monotonic()
        for item in movies + tvshows:
            tf = item.get("trailerFile", "")
            if not tf or item.get("trailerStatus") != "local":
                continue
            t_file = time.monotonic()
            try:
                with open(tf, "rb") as f:
                    f.read(1024 * 1024)  # Head: 1 MB
                    size = os.path.getsize(tf)
                    tail_start = max(0, size - 2 * 1024 * 1024)
                    if tail_start > 1024 * 1024:
                        f.seek(tail_start)
                        f.read(2 * 1024 * 1024)  # Tail: last 2 MB
                count += 1
                ms = int((time.monotonic() - t_file) * 1000)
                if ms >= 500:
                    slow.append((tf, ms))
            except OSError:
                pass
        if count and MTDP_DEBUG:
            total_ms = int((time.monotonic() - t_start) * 1000)
            avg_ms = total_ms / count if count else 0
            print(f"Pre-warmed {count} trailer file(s) in {total_ms} ms (avg {avg_ms:.0f} ms/file, trigger={trigger})")
            if slow:
                slow.sort(key=lambda x: -x[1])
                print(f"[DIAG] {len(slow)} slow files (>=500ms), top 10:")
                for tf, ms in slow[:10]:
                    print(f"[DIAG]   {ms:>6} ms — {os.path.basename(tf)}")
    except Exception:
        pass


def refresh_library_cache():
    """Refresh the stats and library cache in the background. Thread-safe."""
    global _cache_refreshing, _cache_refresh_pending
    if _cache_refreshing:
        _cache_refresh_pending = True
        return
    _cache_refreshing = True
    t = threading.Thread(target=_do_refresh_cache, daemon=True, name="cache-refresh")
    t.start()


def _do_refresh_cache():
    """Actually refresh the cache (runs in background thread)."""
    global _cache_refreshing, _cache_progress, _cache_refresh_pending
    _cache_progress = {"refreshing": True, "phase": "", "current_library": "", "processed": 0, "total": 0}
    try:
        config = _load_yaml(webui._config_path)
        plex = _get_plex_server(config)
        if not plex:
            return

        check_plex_pass = config.get('CHECK_PLEX_PASS_TRAILERS', True)
        movie_libs = config.get('MOVIE_LIBRARIES', [])
        tv_libs = config.get('TV_LIBRARIES', [])

        stats = {
            "total_movies": 0,
            "total_shows": 0,
            "movies_missing_trailers": 0,
            "shows_missing_trailers": 0,
            "movies_local_trailers": 0,
            "shows_local_trailers": 0,
            "movies_skipped_genres": 0,
            "shows_skipped_genres": 0,
            "movies_plexpass_trailers": 0,
            "shows_plexpass_trailers": 0,
            "movies_disk_bytes": 0,
            "shows_disk_bytes": 0,
        }

        movies_list = []
        tvshows_list = []
        _collected_dirs = []  # Pre-collect dirs for allowed-dirs cache

        # Process movies
        for lib in movie_libs:
            lib_name = lib.get('name', '') if isinstance(lib, dict) else lib
            genres_to_skip = lib.get('genres_to_skip', []) if isinstance(lib, dict) else []
            try:
                section = plex.library.section(lib_name)
                _collected_dirs.extend(section.locations)
                movies = section.all()
                stats["total_movies"] += len(movies)

                # Pre-compute set of ratingKeys that match skip genres
                # using section.search() as a fast first pass
                skipped_keys = set()
                genres_to_skip_lower = [g.lower() for g in genres_to_skip]
                if genres_to_skip:
                    for genre_name in genres_to_skip:
                        try:
                            matched = section.search(genre=genre_name)
                            for m in matched:
                                skipped_keys.add(m.ratingKey)
                        except Exception:
                            pass

                _cache_progress.update(phase="movies", current_library=lib_name, processed=0, total=len(movies))
                for idx, movie in enumerate(movies):
                    _cache_progress["processed"] = idx + 1

                    has_local, local_file = _check_local_trailer_movie(movie)
                    has_plexpass, _ = _check_plexpass_trailer(movie) if check_plex_pass else (False, "")
                    trailer_status, trailer_file = _determine_trailer_status(
                        has_local, local_file, has_plexpass, check_plex_pass
                    )

                    # Determine genre skip using multiple methods
                    if genres_to_skip:
                        if movie.ratingKey in skipped_keys:
                            genre_skipped = True
                        else:
                            # Also check genres from section.all() data
                            item_genres = [g.tag.lower() for g in movie.genres] if movie.genres else []
                            if not item_genres:
                                # Genres not loaded - reload item for definitive check
                                try:
                                    movie.reload()
                                    item_genres = [g.tag.lower() for g in movie.genres] if movie.genres else []
                                except Exception:
                                    pass
                            genre_skipped = any(g in item_genres for g in genres_to_skip_lower)
                    else:
                        genre_skipped = False

                    if trailer_status == "local":
                        stats["movies_local_trailers"] += 1
                        try:
                            stats["movies_disk_bytes"] += os.path.getsize(trailer_file)
                        except Exception:
                            pass
                    elif trailer_status == "plexpass":
                        stats["movies_plexpass_trailers"] += 1
                    elif trailer_status == "missing":
                        if genre_skipped:
                            stats["movies_skipped_genres"] += 1
                        else:
                            stats["movies_missing_trailers"] += 1

                    media_path = ""
                    try:
                        media_path = _normalize_path(movie.media[0].parts[0].file)
                    except Exception:
                        pass

                    poster_url = ""
                    try:
                        poster_url = movie.posterUrl
                    except Exception:
                        pass

                    trailer_resolution = _get_trailer_resolution(trailer_file) if trailer_status == "local" else ""
                    trailer_language = _detect_trailer_language(trailer_file) if trailer_status == "local" else ""

                    movies_list.append({
                        "ratingKey": movie.ratingKey,
                        "title": movie.title,
                        "year": movie.year,
                        "addedAt": movie.addedAt.isoformat() if movie.addedAt else "",
                        "summary": movie.summary or "",
                        "genres": [g.tag for g in movie.genres] if movie.genres else [],
                        "actors": [a.tag for a in movie.roles[:10]] if movie.roles else [],
                        "posterUrl": poster_url,
                        "trailerStatus": trailer_status,
                        "trailerFile": trailer_file,
                        "trailerResolution": trailer_resolution,
                        "trailerLanguage": trailer_language,
                        "mediaPath": media_path,
                        "library": lib_name,
                        "genreSkipped": genre_skipped,
                    })
            except Exception:
                pass

        # Process TV shows
        for lib in tv_libs:
            lib_name = lib.get('name', '') if isinstance(lib, dict) else lib
            genres_to_skip = lib.get('genres_to_skip', []) if isinstance(lib, dict) else []
            try:
                section = plex.library.section(lib_name)
                shows = section.all()
                locations = section.locations
                _collected_dirs.extend(locations)
                stats["total_shows"] += len(shows)

                # Pre-compute set of ratingKeys that match skip genres
                skipped_keys = set()
                genres_to_skip_lower = [g.lower() for g in genres_to_skip]
                if genres_to_skip:
                    for genre_name in genres_to_skip:
                        try:
                            matched = section.search(genre=genre_name)
                            for m in matched:
                                skipped_keys.add(m.ratingKey)
                        except Exception:
                            pass

                _cache_progress.update(phase="tvshows", current_library=lib_name, processed=0, total=len(shows))
                for idx, show in enumerate(shows):
                    _cache_progress["processed"] = idx + 1

                    has_local, local_file = _check_local_trailer_show(show, locations)
                    has_plexpass, _ = _check_plexpass_trailer(show) if check_plex_pass else (False, "")
                    trailer_status, trailer_file = _determine_trailer_status(
                        has_local, local_file, has_plexpass, check_plex_pass
                    )

                    # Determine genre skip using multiple methods
                    if genres_to_skip:
                        if show.ratingKey in skipped_keys:
                            genre_skipped = True
                        else:
                            item_genres = [g.tag.lower() for g in show.genres] if show.genres else []
                            if not item_genres:
                                try:
                                    show.reload()
                                    item_genres = [g.tag.lower() for g in show.genres] if show.genres else []
                                except Exception:
                                    pass
                            genre_skipped = any(g in item_genres for g in genres_to_skip_lower)
                    else:
                        genre_skipped = False

                    if trailer_status == "local":
                        stats["shows_local_trailers"] += 1
                        try:
                            stats["shows_disk_bytes"] += os.path.getsize(trailer_file)
                        except Exception:
                            pass
                    elif trailer_status == "plexpass":
                        stats["shows_plexpass_trailers"] += 1
                    elif trailer_status == "missing":
                        if genre_skipped:
                            stats["shows_skipped_genres"] += 1
                        else:
                            stats["shows_missing_trailers"] += 1

                    poster_url = ""
                    try:
                        poster_url = show.posterUrl
                    except Exception:
                        pass

                    media_path = _get_show_folder(show, locations)

                    trailer_resolution = _get_trailer_resolution(trailer_file) if trailer_status == "local" else ""
                    trailer_language = _detect_trailer_language(trailer_file) if trailer_status == "local" else ""

                    tvshows_list.append({
                        "ratingKey": show.ratingKey,
                        "title": show.title,
                        "year": show.year,
                        "addedAt": show.addedAt.isoformat() if show.addedAt else "",
                        "summary": show.summary or "",
                        "genres": [g.tag for g in show.genres] if show.genres else [],
                        "actors": [a.tag for a in show.roles[:10]] if show.roles else [],
                        "posterUrl": poster_url,
                        "trailerStatus": trailer_status,
                        "trailerFile": trailer_file,
                        "trailerResolution": trailer_resolution,
                        "trailerLanguage": trailer_language,
                        "mediaPath": media_path,
                        "library": lib_name,
                        "genreSkipped": genre_skipped,
                    })
            except Exception:
                pass

        with _cache_lock:
            _cache_data["stats"] = stats
            _cache_data["movies"] = movies_list
            _cache_data["tvshows"] = tvshows_list
            _cache_data["last_refreshed"] = datetime.now().isoformat()
            _rebuild_known_trailer_paths()

        _save_cache()

        # Pre-populate allowed-dirs cache so the first trailer stream
        # doesn't need a cold PlexServer connection for path validation.
        if _collected_dirs:
            dirs = []
            if IS_DOCKER:
                for candidate in ['/media', '/data', '/mnt']:
                    if os.path.isdir(candidate):
                        dirs.append(os.path.realpath(candidate))
            for d in _collected_dirs:
                real_d = os.path.realpath(d)
                if real_d not in dirs:
                    dirs.append(real_d)
            _allowed_dirs_cache["dirs"] = dirs
            _allowed_dirs_cache["timestamp"] = time.time()

        print("Library cache refreshed")
        _prewarm_trailer_files(trigger="post-refresh")
    except Exception as e:
        print(f"Cache refresh error: {e}")
    finally:
        _cache_refreshing = False
        _cache_progress.update(refreshing=False, phase="", current_library="", processed=0, total=0)
        if _cache_refresh_pending:
            _cache_refresh_pending = False
            refresh_library_cache()

GITHUB_REPO = "netplexflix/Missing-Trailer-Downloader-For-Plex"


def _get_version():
    from webui import _version
    return _version or "unknown"


class _QuotedDumper(yaml.SafeDumper):
    """YAML dumper that always quotes string values."""
    pass


def _quoted_str(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")


_QuotedDumper.add_representer(str, _quoted_str)


# ── Section header comments for config file ───────────────────────────────
SECTION_HEADERS = {
    'LAUNCH_METHOD': '################################################################################\n##########                         GENERAL:                           ##########\n################################################################################',
    'TV_LIBRARIES': '################################################################################\n##########                      TV LIBRARIES:                         ##########\n################################################################################',
    'MOVIE_LIBRARIES': '################################################################################\n##########                    MOVIE LIBRARIES:                        ##########\n################################################################################',
    'CHECK_PLEX_PASS_TRAILERS': '################################################################################\n##########                   TRAILER SETTINGS:                        ##########\n################################################################################',
    'YT_DLP_CUSTOM_OPTIONS': '################################################################################\n##########                  YT-DLP CUSTOM OPTIONS:                    ##########\n################################################################################',
    'SCHEDULE_TYPE': '################################################################################\n##########                         SCHEDULER:                         ##########\n################################################################################',
}

# ── Config option metadata ─────────────────────────────────────────────────

SETTINGS_OPTIONS = [
    # Connection
    {"key": "PLEX_URL", "type": "string", "default": "http://localhost:32400", "label": "Plex URL", "description": "URL of your Plex Media Server", "section": "Plex Connection"},
    {"key": "PLEX_TOKEN", "type": "string", "default": "", "label": "Plex Token", "description": "Your Plex authentication token", "section": "Plex Connection", "sensitive": True},
    # Scheduler
    {"key": "SCHEDULE_TYPE", "type": "select", "default": "hours", "label": "Schedule Type",
     "description": "",
     "section": "Scheduler", "options": [
        {"value": "hours", "label": "Every X hours"},
        {"value": "cron", "label": "Cron expression"},
    ]},
    {"key": "SCHEDULE_HOURS", "type": "number", "default": 24, "label": "Hours Interval",
     "description": "Run every X hours",
     "section": "Scheduler", "min": 1},
    {"key": "SCHEDULE_CRON", "type": "string", "default": "", "label": "Cron Expression",
     "description": "Standard 5-field cron expression. For help: crontab.guru",
     "description_html": 'Standard 5-field cron expression. For help: <a href="https://crontab.guru/" target="_blank" rel="noopener">crontab.guru</a>',
     "section": "Scheduler"},
    # General
    {"key": "LAUNCH_METHOD", "type": "select", "default": "3", "label": "Launch Method", "description": "What to process on each run", "section": "General", "options": [
        {"value": "0", "label": "Menu (local only)"},
        {"value": "1", "label": "Movies only"},
        {"value": "2", "label": "TV Shows only"},
        {"value": "3", "label": "Both consecutively"},
    ]},
    {"key": "USE_LABELS", "type": "bool", "default": True, "label": "Use Labels", "description": "Use MTDfP labels to track processed items in Plex", "section": "General"},
    # Trailer Settings
    {"key": "CHECK_PLEX_PASS_TRAILERS", "type": "bool", "default": True, "label": "Check Plex Pass Trailers", "description": "Check for existing Plex Pass trailers before downloading", "section": "Trailer Settings"},
    {"key": "DOWNLOAD_TRAILERS", "type": "bool", "default": True, "label": "Download Trailers", "description": "Actually download missing trailers (false = dry-run/list only)", "section": "Trailer Settings"},
    {"key": "PREFERRED_LANGUAGE", "type": "select", "default": "original", "label": "Preferred Language", "description": "Language preference for trailer downloads", "section": "Trailer Settings", "options": [
        {"value": "original", "label": "Original"},
        {"value": "english", "label": "English"},
        {"value": "german", "label": "German"},
        {"value": "french", "label": "French"},
        {"value": "spanish", "label": "Spanish"},
        {"value": "italian", "label": "Italian"},
        {"value": "japanese", "label": "Japanese"},
        {"value": "korean", "label": "Korean"},
        {"value": "portuguese", "label": "Portuguese"},
        {"value": "russian", "label": "Russian"},
        {"value": "chinese", "label": "Chinese"},
    ]},
    {"key": "REFRESH_METADATA", "type": "bool", "default": True, "label": "Refresh Metadata", "description": "Refresh Plex metadata after downloading trailers", "section": "Trailer Settings"},
    {"key": "SHOW_YT_DLP_PROGRESS", "type": "bool", "default": True, "label": "Show yt-dlp Progress", "description": "Show detailed yt-dlp download progress in logs", "section": "Trailer Settings"},
    {"key": "TRAILER_FILE_FORMAT", "type": "select", "default": "mkv", "label": "Trailer File Format", "description": "Container format for downloaded trailers. MP4 recommended for best performance.", "section": "Trailer Settings", "options": [
        {"value": "mkv", "label": "MKV"},
        {"value": "mp4", "label": "MP4"},
    ]},
    {"key": "TRAILER_RESOLUTION_MAX", "type": "select", "default": "1080", "label": "Maximum Trailer Resolution", "description": "Highest resolution to attempt downloading", "section": "Trailer Settings", "options": [
        {"value": "2160", "label": "4K (2160p)"},
        {"value": "1440", "label": "1440p"},
        {"value": "1080", "label": "1080p"},
        {"value": "720", "label": "720p"},
        {"value": "480", "label": "480p"},
        {"value": "360", "label": "360p"},
    ]},
    {"key": "TRAILER_RESOLUTION_MIN", "type": "select", "default": "1080", "label": "Minimum Trailer Resolution", "description": "Lowest acceptable resolution — won't download below this", "section": "Trailer Settings", "options": [
        {"value": "2160", "label": "4K (2160p)"},
        {"value": "1440", "label": "1440p"},
        {"value": "1080", "label": "1080p"},
        {"value": "720", "label": "720p"},
        {"value": "480", "label": "480p"},
        {"value": "360", "label": "360p"},
    ]},
    # yt-dlp
    {"key": "YT_DLP_CUSTOM_OPTIONS", "type": "string_list", "default": [], "label": "yt-dlp Custom Options", "description": "Extra command-line flags passed to yt-dlp", "section": "yt-dlp Custom Options"},
]


# ── Helper functions ───────────────────────────────────────────────────────

def _load_yaml(path):
    """Load a YAML file and return the dict (or empty dict)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data if data else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_yaml(path, data):
    """Save dict to YAML file atomically, re-inserting section headers."""
    tmp_path = path + '.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            for key, value in data.items():
                if key in SECTION_HEADERS:
                    f.write(SECTION_HEADERS[key] + '\n')
                yaml.dump({key: value}, f, Dumper=_QuotedDumper, default_flow_style=False, allow_unicode=True, sort_keys=False)
                f.write('\n')

        os.replace(tmp_path, path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise e


def _get_config_value(config, key, default=None):
    """Get a value from config with type coercion."""
    val = config.get(key)
    if val is None:
        return default
    return val


def _test_plex_connection(url, token, timeout=10):
    """Test Plex connection. Returns (success, message, response_time_ms, server_name)."""
    try:
        start = time.time()
        test_url = url.rstrip('/') + '/'
        headers = {'X-Plex-Token': token, 'Accept': 'application/xml'}
        resp = requests.get(test_url, headers=headers, timeout=timeout)
        elapsed = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            # Extract server friendly name from response
            server_name = "Plex"
            try:
                content_type = resp.headers.get('Content-Type', '')
                if 'json' in content_type:
                    data = resp.json()
                    friendly = data.get('friendlyName') or data.get('MediaContainer', {}).get('friendlyName')
                    if friendly:
                        server_name = friendly
                else:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.text)
                    friendly = root.get('friendlyName')
                    if friendly:
                        server_name = friendly
            except Exception:
                pass
            return True, f"Connected ({elapsed}ms)", elapsed, server_name
        else:
            return False, f"HTTP {resp.status_code}", elapsed, "Plex"
    except requests.exceptions.ConnectionError:
        return False, "Connection refused", 0, "Plex"
    except requests.exceptions.Timeout:
        return False, "Connection timed out", 0, "Plex"
    except Exception as e:
        print(f"Plex connection test error: {e}")
        return False, "Connection failed", 0, "Plex"


def _get_plex_server(config=None):
    """Get a PlexServer instance from config."""
    from plexapi.server import PlexServer
    if config is None:
        config = _load_yaml(webui._config_path)
    url = config.get('PLEX_URL', '')
    token = config.get('PLEX_TOKEN', '')
    if not url or not token:
        return None
    try:
        return PlexServer(url, token)
    except Exception:
        return None


def _remove_all_mtdfp_labels(config=None):
    """Remove MTDfP labels from all items in all configured libraries. Returns count removed."""
    if config is None:
        config = _load_yaml(webui._config_path)
    plex = _get_plex_server(config)
    if not plex:
        return 0
    count = 0
    for lib_list_key in ('MOVIE_LIBRARIES', 'TV_LIBRARIES'):
        for lib_config in config.get(lib_list_key, []):
            lib_name = lib_config.get('name', '')
            if not lib_name:
                continue
            try:
                section = plex.library.section(lib_name)
                labeled_items = section.search(filters={'label': 'MTDfP'})
                for item in labeled_items:
                    try:
                        item.removeLabel('MTDfP')
                        count += 1
                    except Exception:
                        pass
            except Exception:
                pass
    return count


def _get_media_directories(config):
    """Get all media directories from Plex library configs."""
    dirs = []
    plex = _get_plex_server(config)
    if plex:
        movie_libs = config.get('MOVIE_LIBRARIES', [])
        tv_libs = config.get('TV_LIBRARIES', [])
        for lib in movie_libs + tv_libs:
            lib_name = lib.get('name', '') if isinstance(lib, dict) else lib
            try:
                section = plex.library.section(lib_name)
                for loc in section.locations:
                    dirs.append(loc)
            except Exception:
                pass
    return dirs


# ── Path validation (security) ────────────────────────────────────────────
_ALLOWED_VIDEO_EXTS = {'.mkv', '.mp4', '.webm', '.avi', '.mov', '.m4v', '.wmv'}

# Language code to label mapping for trailer filenames
_LANG_CODE_MAP = {
    'de': 'German', 'fr': 'French', 'es': 'Spanish', 'it': 'Italian',
    'ja': 'Japanese', 'ko': 'Korean', 'pt': 'Portuguese', 'ru': 'Russian',
    'zh': 'Chinese', 'en': 'English',
}

# Keywords used to detect whether a video title/channel matches a language
_LANGUAGE_KEYWORDS = {
    'german': ['deutsch', 'german', 'auf deutsch', 'de'],
    'french': ['français', 'francais', 'french', 'vf', 'vostfr', 'fr'],
    'spanish': ['español', 'espanol', 'spanish', 'castellano', 'es'],
    'italian': ['italiano', 'italian', 'it'],
    'japanese': ['日本語', 'japanese', 'jp', 'ja'],
    'korean': ['한국어', 'korean', 'ko'],
    'portuguese': ['português', 'portugues', 'portuguese', 'pt', 'dublado'],
    'russian': ['русский', 'russian', 'ru'],
    'chinese': ['中文', 'chinese', 'zh'],
    'english': ['english', 'en'],
}


def _detect_trailer_language(trailer_file):
    """Extract language code from trailer filename (e.g. 'Title.de-trailer.mkv' → 'de')."""
    if not trailer_file:
        return ""
    basename = os.path.splitext(os.path.basename(trailer_file))[0]  # e.g. "Title (2020).de-trailer"
    if basename.endswith('-trailer'):
        prefix = basename[:-len('-trailer')]  # e.g. "Title (2020).de"
        # Check if prefix ends with a language code
        parts = prefix.rsplit('.', 1)
        if len(parts) == 2 and parts[1] in _LANG_CODE_MAP:
            return parts[1]
    return ""

# Sentinel used to mask sensitive config values in API responses.
# If the frontend sends this back unchanged, the POST handler skips the key.
_SENSITIVE_MASK_PREFIX = "\u2022\u2022\u2022\u2022"  # "••••"

# Set of config keys that are allowed to be written via the settings API.
_ALLOWED_CONFIG_KEYS = {opt["key"] for opt in SETTINGS_OPTIONS}
_SENSITIVE_CONFIG_KEYS = {opt["key"] for opt in SETTINGS_OPTIONS if opt.get("sensitive")}


_allowed_dirs_cache = {"dirs": [], "timestamp": 0}


def _get_allowed_dirs():
    """Return list of allowed media directories, cached for performance.

    The cache is rebuilt at boot (_prepopulate_allowed_dirs) and after every
    scheduled run (_do_refresh_cache), so no TTL-based expiration is needed.
    This avoids a cold Plex API connection on the trailer streaming path.
    The cache can still be explicitly invalidated by clearing the dirs list
    (see _validate_trailer_path fallback).
    """
    if _allowed_dirs_cache["dirs"]:
        return _allowed_dirs_cache["dirs"]

    dirs = []
    # In Docker, include common mount points
    if IS_DOCKER:
        for candidate in ['/media', '/data', '/mnt']:
            if os.path.isdir(candidate):
                dirs.append(os.path.realpath(candidate))

    # Add configured Plex library locations
    try:
        config = _load_yaml(webui._config_path)
        for lib_dir in _get_media_directories(config):
            real_dir = os.path.realpath(lib_dir)
            if real_dir not in dirs:
                dirs.append(real_dir)
    except Exception:
        pass

    # Also add parent directories of any cached trailer files as fallback
    try:
        with _cache_lock:
            for collection in ['movies', 'tvshows']:
                items = _cache_data.get(collection) or []
                for item in items:
                    tf = item.get('trailerFile', '')
                    if tf:
                        parent = os.path.realpath(os.path.dirname(os.path.dirname(tf)))
                        if parent not in dirs:
                            dirs.append(parent)
                    mp = item.get('mediaPath', '')
                    if mp:
                        real_mp = os.path.realpath(os.path.dirname(mp) if os.path.isfile(mp) else mp)
                        if real_mp not in dirs:
                            dirs.append(real_mp)
    except Exception:
        pass

    _allowed_dirs_cache["dirs"] = dirs
    _allowed_dirs_cache["timestamp"] = time.time()
    return dirs


def _validate_trailer_path(filepath):
    """Validate that a file path is a video file within allowed media directories."""
    if not filepath:
        return False

    if filepath in _known_trailer_paths or os.path.normpath(filepath) in _known_trailer_paths:
        return True

    real_path = os.path.realpath(filepath)

    if not os.path.isfile(real_path):
        return False

    ext = os.path.splitext(real_path)[1].lower()
    if ext not in _ALLOWED_VIDEO_EXTS:
        return False

    for allowed_dir in _get_allowed_dirs():
        if real_path.startswith(allowed_dir + os.sep) or real_path.startswith(allowed_dir + '/'):
            return True

    _allowed_dirs_cache["dirs"] = []
    for allowed_dir in _get_allowed_dirs():
        if real_path.startswith(allowed_dir + os.sep) or real_path.startswith(allowed_dir + '/'):
            return True

    return False


def _get_update_status():
    """Return update status dict for the web UI."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        latest = resp.json().get("tag_name", "").lstrip("v")
        ver = _get_version()
        if not latest:
            return {"status": "unknown", "current": ver, "latest": None}

        def parse_version(v):
            return tuple(int(x) for x in v.split('.'))

        try:
            current = parse_version(ver)
            remote = parse_version(latest)
            if remote > current:
                status = "update_available"
            elif current > remote:
                status = "develop_build"
            else:
                status = "up_to_date"
        except Exception:
            status = "update_available" if latest != ver else "up_to_date"

        return {"status": status, "current": ver, "latest": latest}
    except Exception:
        return {"status": "error", "current": _get_version(), "latest": None}


# ── yt-dlp version info ──────────────────────────────────────────────────
_ytdlp_info_cache = {"data": None, "timestamp": 0}
_YTDLP_CACHE_TTL = 300  # 5 minutes


def _get_ytdlp_info():
    """Get yt-dlp version info and check for updates. Cached for 5 minutes."""
    now = time.time()
    if _ytdlp_info_cache["data"] and (now - _ytdlp_info_cache["timestamp"]) < _YTDLP_CACHE_TTL:
        return _ytdlp_info_cache["data"]

    installed_version = None
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            installed_version = result.stdout.strip()
    except Exception:
        pass

    if not installed_version:
        info = {
            "name": "yt-dlp", "service": "ytdlp", "online": False,
            "message": "Not installed", "responseTime": 0,
            "version": None, "latestVersion": None, "updateAvailable": False,
        }
        _ytdlp_info_cache["data"] = info
        _ytdlp_info_cache["timestamp"] = now
        return info

    latest_version = None
    update_available = False
    try:
        resp = requests.get(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
            timeout=5
        )
        resp.raise_for_status()
        latest_version = resp.json().get("tag_name", "").lstrip("v")
        if latest_version and installed_version:
            try:
                installed_parts = tuple(int(x) for x in installed_version.split('.'))
                latest_parts = tuple(int(x) for x in latest_version.split('.'))
                update_available = latest_parts > installed_parts
            except Exception:
                update_available = latest_version != installed_version
    except Exception:
        pass

    info = {
        "name": "yt-dlp", "service": "ytdlp", "online": True,
        "message": f"v{installed_version}", "responseTime": 0,
        "version": installed_version, "latestVersion": latest_version,
        "updateAvailable": update_available,
    }
    _ytdlp_info_cache["data"] = info
    _ytdlp_info_cache["timestamp"] = now
    return info


# ── Manual search helper ──────────────────────────────────────────────────

def _yt_search(query, limit=10):
    """Search YouTube using yt-dlp and return results."""
    import yt_dlp
    results = []
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'ignoreerrors': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            if search_results and 'entries' in search_results:
                for entry in search_results['entries']:
                    if entry is None:
                        continue
                    duration = entry.get('duration', 0) or 0
                    # Determine highest available resolution from formats
                    formats = entry.get('formats') or []
                    max_height = 0
                    max_width = 0
                    for f in formats:
                        h = f.get('height') or 0
                        w = f.get('width') or 0
                        if h > max_height:
                            max_height = h
                            max_width = w
                    results.append({
                        'id': entry.get('id', ''),
                        'title': entry.get('title', ''),
                        'channel': entry.get('channel', entry.get('uploader', '')),
                        'duration': duration,
                        'duration_str': f"{int(duration)//60}:{int(duration)%60:02d}" if duration else '?',
                        'view_count': entry.get('view_count', 0) or 0,
                        'thumbnail': entry.get('thumbnail', ''),
                        'url': entry.get('webpage_url', f"https://www.youtube.com/watch?v={entry.get('id', '')}"),
                        'resolution': _classify_resolution(max_width, max_height) if max_height else '',
                    })
    except Exception as e:
        print(f"yt-dlp search error: {e}")
    return results


def _rename_trailer_with_resolution(filepath):
    """Probe a downloaded trailer's resolution and rename the file to include it."""
    if not filepath or not os.path.isfile(filepath):
        return filepath
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        dims = result.stdout.strip().split(',')
        if len(dims) != 2:
            return filepath
        width, height = int(dims[0]), int(dims[1])
        res_label = _classify_resolution(width, height)
    except Exception:
        return filepath

    directory = os.path.dirname(filepath)
    name, ext = os.path.splitext(os.path.basename(filepath))
    if not name.endswith('-trailer'):
        return filepath

    # Skip if resolution is already in the filename
    if re.search(r'\.\d{3,4}p[.\-]', name):
        return filepath

    prefix = name[:-len('-trailer')]

    # Language codes for detection
    lang_codes = {'de', 'fr', 'es', 'it', 'ja', 'ko', 'pt', 'ru', 'zh', 'en'}

    # Insert resolution before language code (if present) and -trailer
    parts = prefix.rsplit('.', 1)
    if len(parts) == 2 and parts[1] in lang_codes:
        new_name = f"{parts[0]}.{res_label}.{parts[1]}-trailer{ext}"
    else:
        new_name = f"{prefix}.{res_label}-trailer{ext}"

    new_path = os.path.join(directory, new_name)
    try:
        os.rename(filepath, new_path)
        return new_path
    except OSError:
        return filepath


def _rename_trailer_with_lang_tag(filepath, lang_code, lang_codes_map):
    """Rename a downloaded trailer to include the language tag."""
    directory = os.path.dirname(filepath)
    name, ext = os.path.splitext(os.path.basename(filepath))
    if not name.endswith('-trailer'):
        return filepath
    prefix = name[:-len('-trailer')]
    # Check if already has a language code
    parts = prefix.rsplit('.', 1)
    if len(parts) == 2 and parts[1] in lang_codes_map.values():
        return filepath  # Already has a lang tag
    # Insert lang code before -trailer (after resolution label if present)
    new_name = f"{prefix}.{lang_code}-trailer{ext}"
    new_path = os.path.join(directory, new_name)
    try:
        os.rename(filepath, new_path)
        return new_path
    except OSError:
        return filepath


def _download_trailer_for_item(video_url, media_path, title, year, media_type="movie", ignore_quality_min=False):
    """Download a trailer from YouTube and save it alongside the media file."""
    import yt_dlp

    config = _load_yaml(webui._config_path)

    # Normalize path for Docker (Windows Plex → Linux container)
    media_path = _normalize_path(media_path)

    # Determine output path
    # For movies: media_path is a file path (e.g. /media/Movies/Title/Title.mkv)
    #   → trailer goes in the same directory's Trailers subfolder
    # For TV shows: media_path is the show folder (e.g. /media/TV Shows/Title/)
    #   → trailer goes in the show folder's Trailers subfolder
    if media_type == "movie":
        media_dir = os.path.dirname(media_path)
    else:
        # TV show - media_path is already the show folder
        media_dir = media_path.rstrip('/').rstrip('\\')
    trailers_dir = os.path.join(media_dir, 'Trailers')
    try:
        os.makedirs(trailers_dir, exist_ok=True)
    except PermissionError:
        return False, f"Permission denied: '{trailers_dir}'. Please check that your media paths are mounted correctly in Docker."
    except OSError as e:
        return False, f"Cannot create trailer directory: '{trailers_dir}' — {e}. Please check that your media paths are mounted correctly."

    # Clean title for filename (match module behavior: colons → " -")
    safe_title = title.replace(":", " -")
    safe_title = re.sub(r'[<>"/\\|?*]', '', safe_title)

    # Language code — will only be applied to the filename AFTER download
    # if the video title actually matches the preferred language
    lang_codes = {
        'german': 'de', 'french': 'fr', 'spanish': 'es', 'italian': 'it',
        'japanese': 'ja', 'korean': 'ko', 'portuguese': 'pt', 'russian': 'ru',
        'chinese': 'zh', 'english': 'en',
    }
    preferred_lang = config.get('PREFERRED_LANGUAGE', 'original').lower()
    lang_code = lang_codes.get(preferred_lang, '')

    if media_type == "movie" and year:
        output_name = f"{safe_title} ({year})-trailer"
    else:
        # TV shows: no year in filename (matches TV.py behavior)
        output_name = f"{safe_title}-trailer"

    output_path = os.path.join(trailers_dir, output_name)

    # Remove ALL existing trailers for this item (any language variant).
    # This ensures switching language doesn't leave the old trailer behind.
    # Match: "Title (Year)<optional .langcode>-trailer.<ext>" for movies
    #        "Title<optional .langcode>-trailer.<ext>" for TV shows
    if media_type == "movie" and year:
        base_prefix = f"{safe_title} ({year})"
    else:
        base_prefix = safe_title
    for fname in os.listdir(trailers_dir):
        fname_noext, fext = os.path.splitext(fname)
        if fext.lower() not in ('.mkv', '.mp4', '.webm', '.avi', '.mov'):
            continue
        if fname_noext.endswith('-trailer') and fname_noext.startswith(base_prefix):
            # Verify suffix parts are only resolution labels and/or language codes
            stem = fname_noext[:-len('-trailer')]
            suffix = stem[len(base_prefix):]
            # suffix is e.g. "", ".de", ".720p", ".720p.de"
            if not suffix or all(
                p in lang_codes.values() or (p.endswith('p') and p[:-1].isdigit())
                for p in suffix.lstrip('.').split('.') if p
            ):
                try:
                    os.remove(os.path.join(trailers_dir, fname))
                except OSError:
                    pass

    max_res = int(config.get('TRAILER_RESOLUTION_MAX', 1080))
    min_res = int(config.get('TRAILER_RESOLUTION_MIN', 1080))
    file_format = config.get('TRAILER_FILE_FORMAT', 'mkv').lower()
    if file_format not in ('mkv', 'mp4'):
        file_format = 'mkv'
    if min_res > max_res:
        min_res, max_res = max_res, min_res
    if ignore_quality_min:
        fmt = (f'bestvideo[height<={max_res}][ext=mp4]+bestaudio[ext=m4a]/'
               f'bestvideo[height<={max_res}][ext=webm]+bestaudio[ext=webm]/'
               f'bestvideo[height<={max_res}]+bestaudio/'
               f'best[height<={max_res}]/best')
    else:
        fmt = (f'bestvideo[height<={max_res}][height>={min_res}][ext=mp4]+bestaudio[ext=m4a]/'
               f'bestvideo[height<={max_res}][height>={min_res}][ext=webm]+bestaudio[ext=webm]/'
               f'bestvideo[height<={max_res}][height>={min_res}]+bestaudio/'
               f'best[height<={max_res}][height>={min_res}]')
    ydl_opts = {
        'format': fmt,
        'merge_output_format': file_format,
        'outtmpl': output_path + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    # Check for cookies
    if IS_DOCKER:
        cookies_path = '/cookies/cookies.txt'
    else:
        cookies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies', 'cookies.txt')
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path

    # Custom options
    # Blocklist of yt-dlp options that could allow arbitrary code execution,
    # file writes outside media dirs, or credential theft.
    _BLOCKED_YTDLP_OPTS = {
        'exec', 'exec_before_dl', 'exec_before_download',
        'output', 'outtmpl', 'paths', 'batch_file',
        'cookies', 'cookiefile', 'cookies_from_browser', 'cookiesfrombrowser',
        'download_archive', 'config_locations', 'config_location',
        'plugin_dirs', 'write_pages', 'print_to_file',
    }
    custom_opts = config.get('YT_DLP_CUSTOM_OPTIONS', [])
    if custom_opts:
        import shlex
        for option_str in custom_opts:
            parts = shlex.split(option_str)
            for i, part in enumerate(parts):
                if not part.startswith('--'):
                    continue
                key = part[2:].replace('-', '_')
                if key in _BLOCKED_YTDLP_OPTS:
                    print(f"[Security] Blocked dangerous yt-dlp option: --{part[2:]}")
                    continue
                if i + 1 < len(parts) and not parts[i + 1].startswith('--'):
                    value = parts[i + 1]
                    if key == 'extractor_args':
                        if ':' in value:
                            service, args_str = value.split(':', 1)
                            if 'extractor_args' not in ydl_opts:
                                ydl_opts['extractor_args'] = {}
                            if service not in ydl_opts['extractor_args']:
                                ydl_opts['extractor_args'][service] = {}
                            for arg_pair in args_str.split(','):
                                if '=' in arg_pair:
                                    arg_key, arg_val = arg_pair.split('=', 1)
                                    ydl_opts['extractor_args'][service][arg_key] = [arg_val]
                    else:
                        ydl_opts[key] = value

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract video info first to check language match later
            video_info = ydl.extract_info(video_url, download=False)
            video_title = (video_info.get('title', '') or '') if video_info else ''
            video_channel = (video_info.get('channel', '') or video_info.get('uploader', '') or '') if video_info else ''

            ydl.download([video_url])

        # Find the downloaded file and rename to include resolution
        for ext in ['.mkv', '.mp4', '.webm']:
            final_path = output_path + ext
            if os.path.exists(final_path):
                final_path = _rename_trailer_with_resolution(final_path)
                # Apply language tag only if video title/channel matches preferred language
                if lang_code and preferred_lang != 'original':
                    lang_kws = _LANGUAGE_KEYWORDS.get(preferred_lang, [preferred_lang])
                    title_lower = video_title.lower()
                    channel_lower = video_channel.lower()
                    if any(kw in title_lower or kw in channel_lower for kw in lang_kws):
                        final_path = _rename_trailer_with_lang_tag(final_path, lang_code, lang_codes)
                return True, final_path

        return False, "Download completed but file not found"
    except Exception as e:
        err_msg = str(e)
        if not ignore_quality_min and ("Requested format is not available" in err_msg
                                       or "No video formats found" in err_msg
                                       or "format is not available" in err_msg.lower()):
            return False, "QUALITY_TOO_HIGH"
        print(f"Trailer download error: {err_msg}")
        return False, "Download failed"


# ── Route registration ─────────────────────────────────────────────────────

def register_routes(app):
    """Register all Flask routes."""

    # ── Security headers ──────────────────────────────────────────────
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://i.ytimg.com https://*.ytimg.com; "
            "media-src 'self' blob:; "
            "connect-src 'self';"
        )
        # Prevent browser caching of API responses so dashboard always gets fresh data
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
        return response

    # Load any existing cache from disk and trigger background refresh
    _load_cache()
    refresh_library_cache()

    @app.route("/")
    def index():
        return render_template("index.html", version=_get_version())

    # ── Cache refresh ──────────────────────────────────────────────
    @app.route("/api/cache/refresh", methods=["POST"])
    def api_cache_refresh():
        refresh_library_cache()
        return jsonify({"ok": True})

    # ── Status ─────────────────────────────────────────────────────────
    @app.route("/api/status")
    def api_status():
        if webui._scheduler_state:
            result = webui._scheduler_state.get_status_dict()
        else:
            result = {"status": "unknown", "has_schedule": False}
        with _cache_lock:
            result["last_refreshed"] = _cache_data.get("last_refreshed")
        result["cache_progress"] = dict(_cache_progress)
        if webui._trailer_tracker:
            result["scan_progress"] = dict(webui._trailer_tracker.scan_progress)
        return jsonify(result)

    @app.route("/api/scheduler/run-now", methods=["POST"])
    def api_run_now():
        if webui._scheduler_state:
            webui._scheduler_state.request_run()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "No scheduler"}), 400

    @app.route("/api/scheduler/stop", methods=["POST"])
    def api_stop():
        if webui._scheduler_state:
            webui._scheduler_state.request_stop()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "No scheduler"}), 400

    @app.route("/api/scheduler/start", methods=["POST"])
    def api_start():
        if webui._scheduler_state:
            webui._scheduler_state.request_resume()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "No scheduler"}), 400

    # ── Version ────────────────────────────────────────────────────────
    @app.route("/api/update")
    def api_update():
        return jsonify(_get_update_status())

    # ── Config: Settings ───────────────────────────────────────────────
    @app.route("/api/config/settings")
    def api_config_settings():
        config = _load_yaml(webui._config_path)
        result = {"options": [], "libraries": {}}
        for opt in SETTINGS_OPTIONS:
            val = _get_config_value(config, opt["key"], opt["default"])
            entry = {**opt, "value": val}
            # Mask sensitive values (e.g. Plex token) — never send in full
            if opt.get("sensitive") and val:
                suffix = str(val)[-4:] if len(str(val)) >= 4 else ""
                entry["value"] = _SENSITIVE_MASK_PREFIX + suffix
                entry["hasValue"] = True
            elif opt.get("sensitive"):
                entry["value"] = ""
                entry["hasValue"] = False
            result["options"].append(entry)
        # Include library configs
        result["libraries"]["movie"] = config.get("MOVIE_LIBRARIES", [])
        result["libraries"]["tv"] = config.get("TV_LIBRARIES", [])
        return jsonify(result)

    @app.route("/api/config/settings", methods=["POST"])
    def api_save_settings():
        config = _load_yaml(webui._config_path)
        old_check_plex_pass = config.get('CHECK_PLEX_PASS_TRAILERS', True)
        data = request.get_json()
        options = data.get("options", {})
        libraries = data.get("libraries", {})
        for key, value in options.items():
            # Only allow known config keys
            if key not in _ALLOWED_CONFIG_KEYS:
                continue
            # Skip sensitive fields that were not changed (still masked)
            if key in _SENSITIVE_CONFIG_KEYS and isinstance(value, str) and value.startswith(_SENSITIVE_MASK_PREFIX):
                continue
            # Coerce SCHEDULE_HOURS to int (UI may send a string)
            if key == "SCHEDULE_HOURS":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return jsonify({"ok": False, "error": "Hours Interval must be a whole number"}), 400
                if value < 1:
                    return jsonify({"ok": False, "error": "Hours Interval must be >= 1"}), 400
            config[key] = value
        if "movie" in libraries:
            config["MOVIE_LIBRARIES"] = libraries["movie"]
        if "tv" in libraries:
            config["TV_LIBRARIES"] = libraries["tv"]

        # Validate the schedule before persisting so an invalid cron expression
        # never reaches disk.
        sched_type = (config.get("SCHEDULE_TYPE") or "hours").strip().lower()
        sched_hours = config.get("SCHEDULE_HOURS", 24) or 24
        sched_cron = (config.get("SCHEDULE_CRON") or "").strip()
        if sched_type == "cron":
            try:
                from croniter import croniter
                if not sched_cron or not croniter.is_valid(sched_cron):
                    return jsonify({"ok": False, "error": f"Invalid cron expression: {sched_cron or '(empty)'}"}), 400
            except ImportError:
                return jsonify({"ok": False, "error": "croniter package not installed"}), 400

        # If CHECK_PLEX_PASS_TRAILERS was toggled off, remove all MTDfP labels
        # so items with only Plex Pass trailers get re-evaluated on next run
        new_check_plex_pass = config.get('CHECK_PLEX_PASS_TRAILERS', True)
        if old_check_plex_pass and not new_check_plex_pass and config.get('USE_LABELS', False):
            _remove_all_mtdfp_labels(config)
        _save_yaml(webui._config_path, config)

        # Push the new schedule into the live scheduler so the next run is
        # recomputed without a container restart.
        if webui._scheduler_state is not None:
            ok, err = webui._scheduler_state.update_schedule(sched_type, int(sched_hours), sched_cron)
            if not ok:
                return jsonify({"ok": False, "error": err}), 400

        return jsonify({"ok": True})

    # ── Remove all MTDfP labels ───────────────────────────────────────
    @app.route("/api/labels/remove-all", methods=["POST"])
    def api_remove_all_labels():
        import json as _json

        def _stream():
            try:
                config = _load_yaml(webui._config_path)
                plex = _get_plex_server(config)
                if not plex:
                    yield f"data: {_json.dumps({'error': 'Cannot connect to Plex'})}\n\n"
                    return

                # Collect all labeled items across all libraries
                all_items = []
                for lib_list_key in ('MOVIE_LIBRARIES', 'TV_LIBRARIES'):
                    for lib_config in config.get(lib_list_key, []):
                        lib_name = lib_config.get('name', '')
                        if not lib_name:
                            continue
                        try:
                            section = plex.library.section(lib_name)
                            all_items.extend(section.search(filters={'label': 'MTDfP'}))
                        except Exception:
                            pass

                total = len(all_items)
                if total == 0:
                    yield f"data: {_json.dumps({'done': True, 'removed': 0, 'total': 0})}\n\n"
                    return

                removed = 0
                for i, item in enumerate(all_items, 1):
                    try:
                        item.removeLabel('MTDfP')
                        removed += 1
                    except Exception:
                        pass
                    yield f"data: {_json.dumps({'progress': i, 'total': total, 'removed': removed})}\n\n"

                yield f"data: {_json.dumps({'done': True, 'removed': removed, 'total': total})}\n\n"
            except Exception as e:
                yield f"data: {_json.dumps({'error': str(e)})}\n\n"

        return Response(_stream(), mimetype='text/event-stream')

    # ── Connection test ────────────────────────────────────────────────
    @app.route("/api/test/plex", methods=["POST"])
    def api_test_plex():
        data = request.get_json() or {}
        url = data.get("PLEX_URL", "")
        token = data.get("PLEX_TOKEN", "")
        # If the token is still the masked placeholder (user didn't retype it),
        # fall back to the real token stored in config. Otherwise the Unicode
        # bullet characters in the mask would blow up requests' latin-1 header
        # encoder with "ordinal not in range(256)".
        if isinstance(token, str) and token.startswith(_SENSITIVE_MASK_PREFIX):
            stored = _load_yaml(webui._config_path).get("PLEX_TOKEN", "") or ""
            token = stored
        if not url or not token:
            return jsonify({"success": False, "message": "URL and token required"})
        ok, msg, ms, _name = _test_plex_connection(url, token)
        return jsonify({"success": ok, "message": msg, "response_time": ms})

    # ── Dashboard: recent trailers ─────────────────────────────────────
    @app.route("/api/dashboard/recent-trailers")
    def api_dashboard_recent_trailers():
        if webui._trailer_tracker:
            webui._trailer_tracker.reload()
            webui._trailer_tracker.remove_missing()
            items = webui._trailer_tracker.get_recent(30)
            return jsonify({"items": items})
        return jsonify({"items": []})

    # ── Dashboard: services ────────────────────────────────────────────
    @app.route("/api/dashboard/services")
    def api_dashboard_services():
        config = _load_yaml(webui._config_path)
        services = []
        plex_url = config.get('PLEX_URL', '')
        plex_token = config.get('PLEX_TOKEN', '')
        if plex_url and plex_token:
            ok, msg, ms, server_name = _test_plex_connection(plex_url, plex_token, timeout=5)
            services.append({'name': server_name, 'online': ok, 'message': msg, 'responseTime': ms, 'service': 'plex'})
        else:
            services.append({'name': 'Plex', 'online': False, 'message': 'Not configured', 'responseTime': 0, 'service': 'plex'})

        # yt-dlp service info
        services.append(_get_ytdlp_info())

        return jsonify(services)

    # ── yt-dlp update ─────────────────────────────────────────────────
    @app.route("/api/ytdlp/update", methods=["POST"])
    def api_ytdlp_update():
        """Update yt-dlp via pip."""
        try:
            # Clean up any corrupted partial dist-info directories
            import glob as _glob
            site_pkg = os.path.join(os.path.dirname(os.__file__), 'site-packages')
            for bad in _glob.glob(os.path.join(site_pkg, '~t-dlp*')):
                import shutil
                shutil.rmtree(bad, ignore_errors=True)

            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default]"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                # Invalidate cache
                _ytdlp_info_cache["timestamp"] = 0
                # Get new version
                ver_result = subprocess.run(
                    ["yt-dlp", "--version"],
                    capture_output=True, text=True, timeout=5
                )
                new_version = ver_result.stdout.strip() if ver_result.returncode == 0 else "unknown"
                return jsonify({"ok": True, "version": new_version})
            else:
                print(f"yt-dlp update failed: {result.stderr[-500:] if result.stderr else 'unknown'}")
                return jsonify({"ok": False, "error": "yt-dlp update failed"})
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Update timed out (120s)"})
        except Exception as e:
            print(f"yt-dlp update error: {e}")
            return jsonify({"ok": False, "error": "yt-dlp update failed"})

    # ── Dashboard: statistics ──────────────────────────────────────────
    @app.route("/api/dashboard/stats")
    def api_dashboard_stats():
        with _cache_lock:
            cached = _cache_data.get("stats")
        if cached:
            return jsonify(cached)
        # No cache yet - trigger a refresh and return zeroes for now
        refresh_library_cache()
        return jsonify({
            "total_movies": 0,
            "total_shows": 0,
            "movies_missing_trailers": 0,
            "shows_missing_trailers": 0,
            "movies_local_trailers": 0,
            "shows_local_trailers": 0,
            "movies_skipped_genres": 0,
            "shows_skipped_genres": 0,
            "movies_plexpass_trailers": 0,
            "shows_plexpass_trailers": 0,
            "movies_disk_bytes": 0,
            "shows_disk_bytes": 0,
        })

    # ── Dashboard: detailed breakdowns ──────────────────────────────────
    @app.route("/api/dashboard/breakdowns")
    def api_dashboard_breakdowns():
        """Resolution, language, and per-library breakdowns computed from cache."""
        with _cache_lock:
            movies = _cache_data.get("movies")
            tvshows = _cache_data.get("tvshows")
        if movies is None and tvshows is None:
            return jsonify({"resolution": {}, "language": {}, "libraries": []})

        resolution = {}
        language = {}
        lib_map = {}  # lib_name -> {type, total, local, missing, plexpass, skipped}

        for item in (movies or []):
            lib_name = item.get("library", "Unknown")
            if lib_name not in lib_map:
                lib_map[lib_name] = {"type": "movie", "total": 0, "local": 0, "missing": 0, "plexpass": 0, "skipped": 0}
            lib_map[lib_name]["total"] += 1
            status = item.get("trailerStatus", "")
            if status == "local":
                lib_map[lib_name]["local"] += 1
                res = item.get("trailerResolution") or "Unknown"
                resolution[res] = resolution.get(res, 0) + 1
                lang = item.get("trailerLanguage") or ""
                lang_label = _LANG_CODE_MAP.get(lang, lang) if lang else "Original"
                language[lang_label] = language.get(lang_label, 0) + 1
            elif status == "plexpass":
                lib_map[lib_name]["plexpass"] += 1
            elif status == "missing":
                if item.get("genreSkipped"):
                    lib_map[lib_name]["skipped"] += 1
                else:
                    lib_map[lib_name]["missing"] += 1

        for item in (tvshows or []):
            lib_name = item.get("library", "Unknown")
            if lib_name not in lib_map:
                lib_map[lib_name] = {"type": "show", "total": 0, "local": 0, "missing": 0, "plexpass": 0, "skipped": 0}
            lib_map[lib_name]["total"] += 1
            status = item.get("trailerStatus", "")
            if status == "local":
                lib_map[lib_name]["local"] += 1
                res = item.get("trailerResolution") or "Unknown"
                resolution[res] = resolution.get(res, 0) + 1
                lang = item.get("trailerLanguage") or ""
                lang_label = _LANG_CODE_MAP.get(lang, lang) if lang else "Original"
                language[lang_label] = language.get(lang_label, 0) + 1
            elif status == "plexpass":
                lib_map[lib_name]["plexpass"] += 1
            elif status == "missing":
                if item.get("genreSkipped"):
                    lib_map[lib_name]["skipped"] += 1
                else:
                    lib_map[lib_name]["missing"] += 1

        libraries = [{"name": k, **v} for k, v in lib_map.items()]
        return jsonify({"resolution": resolution, "language": language, "libraries": libraries})

    # ── Library: Movies ────────────────────────────────────────────────
    @app.route("/api/library/movies")
    def api_library_movies():
        sort = request.args.get("sort", "title")
        filter_type = request.args.get("filter", "all")

        with _cache_lock:
            cached = _cache_data.get("movies")
        if cached is None:
            # No cache yet - trigger refresh
            refresh_library_cache()
            return jsonify({"items": [], "loading": True})

        items = cached
        if filter_type != "all":
            items = [i for i in items if i.get("trailerStatus") == filter_type]

        if sort == "added":
            items = sorted(items, key=lambda x: x.get("addedAt", ""), reverse=True)
        else:
            items = sorted(items, key=lambda x: x.get("title", "").lower())

        # Build per-library genres_to_skip map
        config = _load_yaml(webui._config_path)
        genres_to_skip_map = {}
        for lib in config.get('MOVIE_LIBRARIES', []):
            if isinstance(lib, dict):
                lib_name = lib.get('name', '')
                skip_genres = [g.lower() for g in lib.get('genres_to_skip', [])]
                if skip_genres:
                    genres_to_skip_map[lib_name] = skip_genres

        return jsonify({"items": items, "genresToSkip": genres_to_skip_map})

    # ── Library: TV Shows ──────────────────────────────────────────────
    @app.route("/api/library/tvshows")
    def api_library_tvshows():
        sort = request.args.get("sort", "title")
        filter_type = request.args.get("filter", "all")

        with _cache_lock:
            cached = _cache_data.get("tvshows")
        if cached is None:
            refresh_library_cache()
            return jsonify({"items": [], "loading": True})

        items = cached
        if filter_type != "all":
            items = [i for i in items if i.get("trailerStatus") == filter_type]

        if sort == "added":
            items = sorted(items, key=lambda x: x.get("addedAt", ""), reverse=True)
        else:
            items = sorted(items, key=lambda x: x.get("title", "").lower())

        # Build per-library genres_to_skip map
        config = _load_yaml(webui._config_path)
        genres_to_skip_map = {}
        for lib in config.get('TV_LIBRARIES', []):
            if isinstance(lib, dict):
                lib_name = lib.get('name', '')
                skip_genres = [g.lower() for g in lib.get('genres_to_skip', [])]
                if skip_genres:
                    genres_to_skip_map[lib_name] = skip_genres

        return jsonify({"items": items, "genresToSkip": genres_to_skip_map})

    # ── Item detail ────────────────────────────────────────────────────
    @app.route("/api/library/item/<int:rating_key>")
    def api_library_item(rating_key):
        config = _load_yaml(webui._config_path)
        check_plex_pass = config.get('CHECK_PLEX_PASS_TRAILERS', True)
        plex = _get_plex_server(config)
        if not plex:
            return jsonify({"error": "Cannot connect to Plex"}), 400

        try:
            item = plex.fetchItem(rating_key)
        except Exception:
            return jsonify({"error": "Item not found"}), 404

        poster_url = ""
        try:
            poster_url = item.posterUrl
        except Exception:
            pass

        # Determine media path
        media_path = ""
        if item.type == 'movie':
            try:
                media_path = _normalize_path(item.media[0].parts[0].file)
            except Exception:
                pass
            has_local, local_file = _check_local_trailer_movie(item)
        else:
            # TV show
            try:
                section = plex.library.sectionByID(item.librarySectionID)
                locations = section.locations
                media_path = _get_show_folder(item, locations)
                has_local, local_file = _check_local_trailer_show(item, locations)
            except Exception:
                has_local, local_file = False, ""

        # Check local first, then Plex Pass
        has_plexpass, plexpass_extra_key = _check_plexpass_trailer(item) if check_plex_pass else (False, "")
        trailer_status, trailer_file = _determine_trailer_status(
            has_local, local_file, has_plexpass, check_plex_pass
        )

        trailer_resolution = _get_trailer_resolution(trailer_file) if trailer_status == "local" else ""

        result = {
            "ratingKey": item.ratingKey,
            "type": item.type,
            "title": item.title,
            "year": getattr(item, 'year', None),
            "summary": item.summary or "",
            "genres": [g.tag for g in item.genres] if hasattr(item, 'genres') and item.genres else [],
            "actors": [{"name": a.tag, "role": a.role, "thumb": a.thumb} for a in (item.roles[:10] if hasattr(item, 'roles') and item.roles else [])],
            "posterUrl": poster_url,
            "rating": getattr(item, 'rating', None),
            "contentRating": getattr(item, 'contentRating', None),
            "duration": getattr(item, 'duration', None),
            "studio": getattr(item, 'studio', None),
            "trailerStatus": trailer_status,
            "trailerFile": trailer_file,
            "trailerResolution": trailer_resolution,
            "trailerLanguage": _detect_trailer_language(trailer_file) if trailer_status == "local" else "",
            "plexpassExtraKey": plexpass_extra_key if trailer_status == "plexpass" else "",
            "mediaPath": media_path,
        }
        return jsonify(result)

    # ── Manual search ──────────────────────────────────────────────────
    @app.route("/api/search/trailer", methods=["POST"])
    def api_search_trailer():
        data = request.get_json() or {}
        title = data.get("title", "")
        year = data.get("year", "")
        media_type = data.get("type", "movie")
        offset = max(0, int(data.get("offset", 0)))
        if not title:
            return jsonify({"results": [], "error": "Title required"})

        type_label = "movie trailer" if media_type == "movie" else "TV show trailer"
        # Normalise smart/curly quotes to ASCII for better YouTube search results
        clean_title = title.replace('\u2018', "'").replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')

        # Include preferred language in search query so results match user preference
        config = _load_yaml(webui._config_path)
        preferred_lang = config.get('PREFERRED_LANGUAGE', 'original').lower()
        lang_keyword = preferred_lang if preferred_lang not in ('original', '') else ''
        query = f"{clean_title} {year} official {type_label} {lang_keyword}".strip()
        page_size = 10
        results = _yt_search(query, limit=offset + page_size)
        page = results[offset:]
        return jsonify({"results": page, "has_more": len(page) == page_size})

    # ── Manual download ────────────────────────────────────────────────
    @app.route("/api/download/trailer", methods=["POST"])
    def api_download_trailer():
        data = request.get_json() or {}
        video_url = data.get("url", "")
        media_path = data.get("mediaPath", "")
        title = data.get("title", "")
        year = data.get("year", "")
        rating_key = data.get("ratingKey", "")
        poster_url = data.get("posterUrl", "")
        media_type = data.get("type", "movie")

        skip_quality_min = data.get("skipQualityMin", False)

        if not video_url or not media_path or not title:
            return jsonify({"ok": False, "error": "Missing required fields"})

        success, result = _download_trailer_for_item(video_url, media_path, title, year, media_type, ignore_quality_min=skip_quality_min)

        if success:
            # Track the download
            if webui._trailer_tracker:
                webui._trailer_tracker.add_trailer(
                    file_path=result,
                    title=title,
                    year=str(year),
                    media_type=media_type,
                    plex_rating_key=str(rating_key),
                    poster_url=poster_url,
                )

            # Refresh Plex metadata
            config = _load_yaml(webui._config_path)
            if config.get('REFRESH_METADATA', True) and rating_key:
                try:
                    plex = _get_plex_server(config)
                    if plex:
                        item = plex.fetchItem(int(rating_key))
                        item.refresh()
                except Exception:
                    pass

            # Update cache for this specific item (no full rebuild needed)
            if rating_key:
                _update_cache_item_status(rating_key, "local", result)

            return jsonify({"ok": True, "path": result})
        else:
            return jsonify({"ok": False, "error": result})

    # ── Delete trailer ──────────────────────────────────────────────────
    @app.route("/api/trailer/delete", methods=["POST"])
    def api_delete_trailer():
        data = request.get_json() or {}
        trailer_file = data.get("trailerFile", "")
        rating_key = data.get("ratingKey", "")

        if not trailer_file:
            return jsonify({"ok": False, "error": "No trailer file specified"})

        if not _validate_trailer_path(trailer_file):
            return jsonify({"ok": False, "error": "Invalid trailer path"}), 403

        try:
            os.remove(trailer_file)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to delete: {e}"})

        # Update cache immediately (fast)
        if rating_key:
            _update_cache_item_status(int(rating_key), "missing", "")

            # Plex API calls (fetchItem, removeLabel, refresh) are slow network
            # operations — run them in a background thread so the response returns
            # immediately after the file is deleted.
            def _plex_cleanup(rk):
                try:
                    config = _load_yaml(webui._config_path)
                    plex = _get_plex_server(config)
                    if plex:
                        item = plex.fetchItem(rk)
                        if config.get('USE_LABELS', False):
                            try:
                                item.removeLabel('MTDfP')
                            except Exception:
                                pass
                        if config.get('REFRESH_METADATA', True):
                            item.refresh()
                except Exception:
                    pass
            threading.Thread(target=_plex_cleanup, args=(int(rating_key),), daemon=True).start()

        return jsonify({"ok": True})

    # ── Bulk delete trailers ───────────────────────────────────────────
    @app.route("/api/trailer/bulk-delete", methods=["POST"])
    def api_bulk_delete_trailers():
        data = request.get_json() or {}
        items = data.get("items", [])

        if not isinstance(items, list) or not items:
            return jsonify({"ok": False, "error": "No items specified"}), 400

        if len(items) > 500:
            return jsonify({"ok": False, "error": "Too many items (max 500)"}), 400

        # Load config and Plex connection once for the entire batch
        config = _load_yaml(webui._config_path)
        plex = _get_plex_server(config)
        use_labels = config.get('USE_LABELS', False)
        refresh_metadata = config.get('REFRESH_METADATA', True)

        results = []
        deleted = 0
        failed = 0
        deleted_rating_keys = []

        for entry in items:
            rating_key = entry.get("ratingKey", "")
            trailer_file = entry.get("trailerFile", "")
            result = {"ratingKey": rating_key}

            if not trailer_file or not _validate_trailer_path(trailer_file):
                result["ok"] = False
                result["error"] = "Invalid trailer path"
                failed += 1
                results.append(result)
                continue

            try:
                os.remove(trailer_file)
            except Exception as e:
                result["ok"] = False
                result["error"] = f"Failed to delete: {e}"
                failed += 1
                results.append(result)
                continue

            # Update cache immediately (fast)
            if rating_key:
                _update_cache_item_status(int(rating_key), "missing", "")
                deleted_rating_keys.append(int(rating_key))

            result["ok"] = True
            deleted += 1
            results.append(result)

        # Plex API calls (fetchItem, removeLabel, refresh) are slow network
        # operations — run them in a background thread so the response returns
        # immediately after files are deleted.
        if deleted_rating_keys and plex:
            def _plex_cleanup():
                for rk in deleted_rating_keys:
                    try:
                        plex_item = plex.fetchItem(rk)
                        if use_labels:
                            try:
                                plex_item.removeLabel('MTDfP')
                            except Exception:
                                pass
                        if refresh_metadata:
                            plex_item.refresh()
                    except Exception:
                        pass
            threading.Thread(target=_plex_cleanup, daemon=True).start()

        return jsonify({"ok": True, "results": results, "deleted": deleted, "failed": failed})

    # ── Serve trailer video for playback ───────────────────────────────
    # Formats that browsers can play natively via <video>
    NATIVE_VIDEO_EXTS = {'.mp4', '.webm'}

    @app.route("/api/trailer/stream")
    def api_trailer_stream():
        t_req = time.monotonic()
        filepath = request.args.get("path", "")
        range_hdr = request.headers.get('Range', '')

        t_validate = time.monotonic()
        valid = _validate_trailer_path(filepath)
        validate_ms = int((time.monotonic() - t_validate) * 1000)

        if not filepath or not valid:
            if MTDP_DEBUG:
                print(f"[DIAG] stream 404 validate_ms={validate_ms} path={filepath!r}")
            return "Not found", 404

        ext = os.path.splitext(filepath)[1].lower()
        fname = os.path.basename(filepath)

        # Non-native formats (mkv, avi, mov, etc.) are remuxed to MP4 via
        # ffmpeg so the browser can play them.  The remux is copy-only (no
        # re-encoding) so it is fast and lossless.
        if ext not in NATIVE_VIDEO_EXTS:
            if MTDP_DEBUG:
                print(f"[DIAG] stream remux file={fname} validate_ms={validate_ms} range={range_hdr!r}")
            return _stream_remuxed(filepath)

        # Native formats – serve the file directly with range support
        mime = 'video/webm' if ext == '.webm' else 'video/mp4'
        return _stream_file(filepath, mime, fname=fname, validate_ms=validate_ms,
                            range_hdr=range_hdr, t_req=t_req)

    def _stream_file(filepath, mime, fname="", validate_ms=0, range_hdr="", t_req=None):
        """Serve a file directly with HTTP Range support."""
        if t_req is None:
            t_req = time.monotonic()
        t_size = time.monotonic()
        file_size = os.path.getsize(filepath)
        getsize_ms = int((time.monotonic() - t_size) * 1000)
        range_header = request.headers.get('Range')

        if range_header:
            byte_start = 0
            byte_end = file_size - 1
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                byte_start = int(match.group(1))
                if match.group(2):
                    byte_end = int(match.group(2))

            content_length = byte_end - byte_start + 1

            def generate():
                t_open_start = time.monotonic()
                first_chunk_ms = None
                bytes_yielded = 0
                status = "ok"
                try:
                    with open(filepath, 'rb') as f:
                        open_ms = int((time.monotonic() - t_open_start) * 1000)
                        f.seek(byte_start)
                        t_first = time.monotonic()
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if first_chunk_ms is None:
                                first_chunk_ms = int((time.monotonic() - t_first) * 1000)
                            if not data:
                                break
                            remaining -= len(data)
                            bytes_yielded += len(data)
                            yield data
                except OSError as e:
                    status = f"oserr:{e}"
                except GeneratorExit:
                    status = "client-closed"
                finally:
                    if MTDP_DEBUG:
                        total_ms = int((time.monotonic() - t_req) * 1000)
                        print(f"[DIAG] stream 206 file={fname} validate_ms={validate_ms} "
                              f"getsize_ms={getsize_ms} range={range_hdr!r} open_ms={open_ms} "
                              f"first_chunk_ms={first_chunk_ms} bytes={bytes_yielded} "
                              f"total_ms={total_ms} status={status}")

            response = Response(generate(), 206, mimetype=mime)
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Content-Length'] = content_length
            response.headers['Accept-Ranges'] = 'bytes'
            return response
        else:
            def generate():
                t_open_start = time.monotonic()
                first_chunk_ms = None
                bytes_yielded = 0
                status = "ok"
                try:
                    with open(filepath, 'rb') as f:
                        open_ms = int((time.monotonic() - t_open_start) * 1000)
                        t_first = time.monotonic()
                        while True:
                            data = f.read(8192)
                            if first_chunk_ms is None:
                                first_chunk_ms = int((time.monotonic() - t_first) * 1000)
                            if not data:
                                break
                            bytes_yielded += len(data)
                            yield data
                except OSError as e:
                    status = f"oserr:{e}"
                except GeneratorExit:
                    status = "client-closed"
                finally:
                    if MTDP_DEBUG:
                        total_ms = int((time.monotonic() - t_req) * 1000)
                        print(f"[DIAG] stream 200 file={fname} validate_ms={validate_ms} "
                              f"getsize_ms={getsize_ms} open_ms={open_ms} "
                              f"first_chunk_ms={first_chunk_ms} bytes={bytes_yielded} "
                              f"total_ms={total_ms} status={status}")

            response = Response(generate(), 200, mimetype=mime)
            response.headers['Content-Length'] = file_size
            response.headers['Accept-Ranges'] = 'bytes'
            return response

    def _stream_remuxed(filepath):
        """Remux a non-native video file to MP4 on-the-fly via ffmpeg."""
        cmd = [
            'ffmpeg',
            '-i', filepath,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-movflags', 'frag_keyframe+empty_moov+faststart',
            '-f', 'mp4',
            '-loglevel', 'error',
            'pipe:1',
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            # ffmpeg not installed – fall back to direct serve
            return _stream_file(filepath, 'video/x-matroska')

        def generate():
            try:
                while True:
                    data = proc.stdout.read(8192)
                    if not data:
                        break
                    yield data
            except (OSError, GeneratorExit):
                pass
            finally:
                proc.terminate()
                proc.stdout.close()
                proc.wait()

        return Response(generate(), 200, mimetype='video/mp4')

    # ── Plex Pass trailer stream proxy ────────────────────────────────
    @app.route("/api/trailer/plex-stream/<int:extra_rating_key>")
    def api_trailer_plex_stream(extra_rating_key):
        """Proxy-stream a Plex Pass trailer via the Plex transcode endpoint."""
        config = _load_yaml(webui._config_path)
        plex_url = config.get('PLEX_URL', '').rstrip('/')
        plex_token = config.get('PLEX_TOKEN', '')
        if not plex_url or not plex_token:
            return "Not configured", 404

        try:
            plex = _get_plex_server(config)
            if not plex:
                return "Cannot connect to Plex", 502

            extra = plex.fetchItem(int(extra_rating_key))
            stream_url = extra.getStreamURL()

            # Proxy the stream from Plex to the client
            resp = requests.get(stream_url, stream=True, timeout=30)
            if resp.status_code != 200:
                return "Plex stream unavailable", 502

            content_type = resp.headers.get('Content-Type', 'video/mp4')

            def generate():
                try:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                except Exception:
                    return
                finally:
                    resp.close()

            return Response(generate(), 200, mimetype=content_type)
        except Exception as e:
            print(f"Plex stream error: {e}")
            return "Stream error", 500

    # ── Plex poster proxy ──────────────────────────────────────────────
    @app.route("/api/plex/poster/<int:rating_key>")
    def api_plex_poster(rating_key):
        config = _load_yaml(webui._config_path)
        plex_url = config.get('PLEX_URL', '').rstrip('/')
        plex_token = config.get('PLEX_TOKEN', '')
        if not plex_url or not plex_token:
            return "Not configured", 404
        # SSRF protection: only allow http/https and block cloud metadata IPs
        try:
            from urllib.parse import urlparse
            parsed = urlparse(plex_url)
            if parsed.scheme not in ('http', 'https'):
                return "Invalid Plex URL", 400
            if parsed.hostname in ('169.254.169.254', 'metadata.google.internal'):
                return "Invalid Plex URL", 400
        except Exception:
            return "Invalid Plex URL", 400
        try:
            thumb_url = f"{plex_url}/library/metadata/{rating_key}/thumb"
            resp = requests.get(thumb_url, headers={'X-Plex-Token': plex_token}, timeout=10, stream=True)
            if resp.status_code == 200:
                poster_resp = Response(resp.content, mimetype=resp.headers.get('Content-Type', 'image/jpeg'))
                poster_resp.headers['Cache-Control'] = 'public, max-age=86400'  # 24h browser cache
                return poster_resp
        except Exception:
            pass
        return "Not found", 404

    # ── Log ────────────────────────────────────────────────────────────
    @app.route("/api/log")
    def api_log():
        """Return the last N lines from the log file."""
        limit = request.args.get("limit", 500, type=int)
        _http_log_re = re.compile(r'^\d+\.\d+\.\d+\.\d+\s+-\s+-\s+\[.*?\]\s+"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/api/')
        project_root = os.path.dirname(os.path.dirname(__file__))
        log_paths = [
            os.path.join(project_root, "logs", "mtdp.log"),
            os.path.join("logs", "mtdp.log"),
            os.path.join("/app", "logs", "mtdp.log"),
        ]
        for log_path in log_paths:
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.readlines()
                    filtered = [l for l in lines if not _http_log_re.match(l)]
                    return jsonify({"lines": filtered[-limit:]})
                except Exception:
                    pass
        return jsonify({"lines": []})

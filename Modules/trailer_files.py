"""Trailer filename helpers shared by Movies.py, TV.py and the web UI.

yt-dlp downloads video and audio as separate pieces (Title-trailer.f137.mp4,
Title-trailer.f140.m4a, plus .part/.ytdl files while in progress) and merges
them into Title-trailer.<ext>. When a stream fails the pieces stay behind.
These helpers recognise and remove them so they are never mistaken for a
trailer, and never resumed or merged into the next download attempt.
"""

import os
import re
import time

# Intermediate files yt-dlp writes for our "<name>-trailer.%(ext)s" template:
#   -trailer.f137.mp4, -trailer.f140-drc.m4a        format pieces awaiting the merge
#   -trailer.f137.mp4.part(-Frag3), -trailer...ytdl  unfinished downloads
#   -trailer.temp.mkv                                merger output in progress
#   -trailer.mp4.part                                unfinished single-file download
PARTIAL_DOWNLOAD_RE = re.compile(
    r'-trailer\.(?:f\d[\w-]*\.\w+|temp\.\w+|\w+)(?:\.part(?:-Frag\d+(?:\.part)?)?|\.ytdl)$'
    r'|-trailer\.(?:f\d[\w-]*|temp)\.\w+$',
    re.IGNORECASE,
)

# Age (seconds) a leftover must have before a sweep outside our own download
# removes it, so a download still running elsewhere is left alone.
LEFTOVER_MIN_AGE = 600


def is_partial_download(path):
    """True if path (a bare name or a full path) is a yt-dlp leftover piece."""
    return bool(path) and PARTIAL_DOWNLOAD_RE.search(path) is not None


def is_partial_extra(extra):
    """True if every file behind a Plex trailer extra is a yt-dlp leftover piece.

    Plex indexes any video in a Trailers folder, including a video-only
    .f137.mp4 piece, so such an extra must not count as a trailer.
    """
    files = [getattr(part, 'file', None)
             for media in (getattr(extra, 'media', None) or [])
             for part in (getattr(media, 'parts', None) or [])]
    files = [f for f in files if f]
    return bool(files) and all(is_partial_download(f) for f in files)


def sanitize_title_for_filename(title):
    """Make a Plex title safe to use in a trailer filename.

    ':' becomes ' -' (as before) and '/' or '\\' become '-' so a title like
    'Face/Off' can't create a subfolder. The other characters Windows and SMB
    shares reject are dropped; Docker Desktop would otherwise store '?' as a
    private-use character that shows up as junk in Plex.
    """
    name = title.replace(":", " -")
    name = re.sub(r'[/\\]', '-', name)
    name = re.sub(r'[<>"|?*]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def cleanup_partial_downloads(folder, stem=None, min_age=0):
    """Delete yt-dlp leftover pieces directly inside folder. Returns the removed names.

    stem limits the cleanup to one download's pieces (names starting with
    "<stem>."). min_age skips files modified within the last min_age seconds.
    """
    removed = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return removed
    now = time.time()
    for name in entries:
        if not is_partial_download(name):
            continue
        if stem and not name.startswith(stem + "."):
            continue
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path):
                continue
            if min_age and now - os.path.getmtime(path) < min_age:
                continue
            os.remove(path)
            removed.append(name)
            print(f"Removed leftover download piece: {name}")
        except OSError as e:
            print(f"Failed to remove leftover download piece '{name}': {e}")
    return removed


def remove_empty_dir(path, min_age=0):
    """Remove path if it is an empty directory not modified in the last min_age seconds."""
    try:
        if os.listdir(path):
            return False
        if min_age and time.time() - os.path.getmtime(path) < min_age:
            return False
        os.rmdir(path)
        return True
    except OSError:
        return False


def sweep_trailers_folder(trailers_folder):
    """Clear old yt-dlp leftovers from an item's Trailers folder, and the folder if it ends up empty."""
    try:
        # Taken before the cleanup, which itself bumps the folder's mtime
        folder_age = time.time() - os.path.getmtime(trailers_folder)
    except OSError:
        return
    cleanup_partial_downloads(trailers_folder, min_age=LEFTOVER_MIN_AGE)
    if folder_age >= LEFTOVER_MIN_AGE and remove_empty_dir(trailers_folder):
        print(f"Removed empty Trailers folder: {trailers_folder}")

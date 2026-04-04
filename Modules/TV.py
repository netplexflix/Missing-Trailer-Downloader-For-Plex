import os
import sys
import yaml
import time
import requests.exceptions
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime
import shlex
from pathlib import Path

VERSION= "2025.11.24601"

# Set up logging
logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logs", "TV Shows")
os.makedirs(logs_dir, exist_ok=True)
log_file = os.path.join(logs_dir, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

class Logger:
    def __init__(self, log_file):
        self.terminal = sys.stdout
        self.log = open(log_file, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger(log_file)
sys.stderr = Logger(log_file)

# Clean up old logs
def clean_old_logs():
    log_files = sorted(
        [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.startswith("log_")],
        key=os.path.getmtime
    )
    while len(log_files) > 31:
        os.remove(log_files.pop(0))

clean_old_logs()

# ANSI color codes
GREEN = '\033[32m'
ORANGE = '\033[33m'
BLUE = '\033[34m'
RED = '\033[31m'
RESET = '\033[0m'

def print_colored(text, color, end="\n"):
    colors = {'red': RED, 'green': GREEN, 'blue': BLUE, 'yellow': ORANGE, 'white': RESET}
    print(f"{colors.get(color, RESET)}{text}{RESET}", end=end)

# Check if running in Docker
IS_DOCKER = os.environ.get('IS_DOCKER', 'false').lower() == 'true'

# --- Configuration ---
if IS_DOCKER:
    config_path = '/config/config.yml'
else:
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yml')

with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')

# Handle multiple libraries configuration
TV_LIBRARIES = config.get('TV_LIBRARIES', [])
if not TV_LIBRARIES:
    TV_LIBRARY_NAME = config.get('TV_LIBRARY_NAME')
    TV_GENRES_TO_SKIP = config.get('TV_GENRES_TO_SKIP', [])
    if TV_LIBRARY_NAME:
        TV_LIBRARIES = [{"name": TV_LIBRARY_NAME, "genres_to_skip": TV_GENRES_TO_SKIP}]

REFRESH_METADATA = config.get('REFRESH_METADATA')
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
CHECK_PLEX_PASS_TRAILERS = config.get('CHECK_PLEX_PASS_TRAILERS', True)
USE_LABELS = config.get('USE_LABELS', False)
YT_DLP_CUSTOM_OPTIONS = config.get('YT_DLP_CUSTOM_OPTIONS', [])

PLEX_RETRY_ATTEMPTS = 5
PLEX_RETRY_DELAY = 15  # seconds between retries

def plex_call(fn, *args, label="Plex API call", **kwargs):
    """Call a Plex API function with retry on timeout. Returns None on persistent failure."""
    for attempt in range(1, PLEX_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            if attempt < PLEX_RETRY_ATTEMPTS:
                print_colored(
                    f"  {label} timed out (attempt {attempt}/{PLEX_RETRY_ATTEMPTS}), "
                    f"retrying in {PLEX_RETRY_DELAY}s...", 'yellow'
                )
                time.sleep(PLEX_RETRY_DELAY)
            else:
                print_colored(f"  {label} failed after {PLEX_RETRY_ATTEMPTS} attempts: {e}", 'red')
                return None
        except Exception as e:
            print_colored(f"  {label} unexpected error: {e}", 'red')
            return None

def get_cookies_path():
    if IS_DOCKER:
        cookies_folder = Path('/cookies')
    else:
        cookies_folder = Path(__file__).parent.parent / 'cookies'
    cookies_file = cookies_folder / 'cookies.txt'
    if cookies_file.exists() and cookies_file.is_file():
        return str(cookies_file)
    return None

def parse_ytdlp_options(options_list):
    parsed_opts = {}
    for option_str in options_list:
        parts = shlex.split(option_str)
        for i, part in enumerate(parts):
            if not part.startswith('--'):
                continue
            key = part[2:].replace('-', '_')
            if i + 1 < len(parts) and not parts[i + 1].startswith('--'):
                value = parts[i + 1]
                if key == 'extractor_args':
                    if ':' in value:
                        service, args_str = value.split(':', 1)
                        if 'extractor_args' not in parsed_opts:
                            parsed_opts['extractor_args'] = {}
                        if service not in parsed_opts['extractor_args']:
                            parsed_opts['extractor_args'][service] = {}
                        for arg_pair in args_str.split(','):
                            if '=' in arg_pair:
                                arg_key, arg_val = arg_pair.split('=', 1)
                                parsed_opts['extractor_args'][service][arg_key] = [arg_val]
                else:
                    if value.lower() in ('true', 'yes'):
                        parsed_opts[key] = True
                    elif value.lower() in ('false', 'no'):
                        parsed_opts[key] = False
                    elif value.isdigit():
                        parsed_opts[key] = int(value)
                    else:
                        parsed_opts[key] = value
            else:
                if key.startswith('no_'):
                    actual_key = key[3:]
                    parsed_opts[actual_key] = False
                else:
                    parsed_opts[key] = True
    return parsed_opts

# Resolve cookies path once at startup — reused in download_trailer()
cookies_path = get_cookies_path()
if cookies_path:
    print(f"{GREEN}Found cookies file: {cookies_path}{RESET}")

plex = PlexServer(PLEX_URL, PLEX_TOKEN)

print("\nConfiguration for this run:")
print(f"TV_LIBRARIES: {[lib['name'] for lib in TV_LIBRARIES]}")
for library in TV_LIBRARIES:
    genres_to_skip = library.get('genres_to_skip', [])
    print(f"  {library['name']} - GENRES_TO_SKIP: {', '.join(genres_to_skip)}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"USE_LABELS: {GREEN}true{RESET}" if USE_LABELS else f"USE_LABELS: {ORANGE}false{RESET}")
print(f"PLEX_RETRY_ATTEMPTS: {PLEX_RETRY_ATTEMPTS}, PLEX_RETRY_DELAY: {PLEX_RETRY_DELAY}s")
if YT_DLP_CUSTOM_OPTIONS:
    print(f"YT_DLP_CUSTOM_OPTIONS: {', '.join(YT_DLP_CUSTOM_OPTIONS)}")
if IS_DOCKER:
    print(f"Running in: {GREEN}Docker Container{RESET}")

# Use sets for O(1) membership checks
shows_with_downloaded_trailers = {}
shows_download_errors = set()
shows_skipped = []
shows_missing_trailers = set()

def add_mtdfp_label(show, context=""):
    try:
        show.edit(**{'label.locked': 0})
        existing_labels = [label.tag for label in (show.labels or [])]
        if 'MTDfP' not in existing_labels:
            show.addLabel('MTDfP')
            context_text = f" ({context})" if context else ""
            print_colored(f"Added MTDfP label to '{show.title}'{context_text}", 'green')
        else:
            print_colored(f"TV show '{show.title}' already has MTDfP label", 'blue')
    except Exception as e:
        print_colored(f"Failed to add MTDfP label to '{show.title}': {e}", 'red')

def short_videos_only(info_dict, incomplete=False):
    duration = info_dict.get('duration')
    print(f"Video duration check - Title: {info_dict.get('title', 'Unknown')}")
    print(f"Duration: {duration if duration is not None else 'Not available'} seconds")
    if duration is None:
        print("Warning: Could not determine video duration before download")
        return None
    if duration > 300:
        print(f"Rejecting video: Duration {duration} seconds exceeds 5 minute limit")
        return f"Skipping video because it's too long ({duration} seconds)"
    print(f"Accepting video: Duration {duration} seconds is within 5 minute limit")
    return None

def normalize_path_for_docker(path):
    if not IS_DOCKER:
        return path
    if path.startswith('/'):
        return path
    import re
    drive_match = re.match(r'^([A-Za-z]):', path)
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        path_without_drive = path[2:]
        path_normalized = path_without_drive.replace('\\', '/')
        result = f'/{drive_letter}{path_normalized}'
        print(f"Path normalized: {path} -> {result}")
        return result
    return path.replace('\\', '/')

def has_local_trailer(show_directory):
    if not os.path.isdir(show_directory):
        print(f"Warning: Cannot access directory: {show_directory}")
        return False
    try:
        contents = os.listdir(show_directory)
    except OSError as e:
        print(f"Warning: Error listing directory '{show_directory}': {e}")
        return False
    for f in contents:
        if f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            name_without_ext, _ = os.path.splitext(f.lower())
            if name_without_ext.endswith("-trailer"):
                return True
    trailers_subfolder = os.path.join(show_directory, "Trailers")
    if os.path.isdir(trailers_subfolder):
        try:
            sub_contents = os.listdir(trailers_subfolder)
        except OSError as e:
            print(f"Warning: Error listing directory '{trailers_subfolder}': {e}")
            return False
        for sub_f in sub_contents:
            if sub_f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
                return True
    return False

def download_trailer(show_title, show_directory):
    sanitized_title = show_title.replace(":", " -")
    key_terms = show_title.lower().split(":")
    main_title = key_terms[0].strip()
    subtitle = key_terms[1].strip() if len(key_terms) > 1 else None
    search_query = f"ytsearch10:{show_title} TV show official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"
    trailers_directory = os.path.join(show_directory, 'Trailers')
    os.makedirs(trailers_directory, exist_ok=True)
    output_filename = os.path.join(trailers_directory, f"{sanitized_title}-trailer.%(ext)s")
    final_trailer_filename = os.path.join(trailers_directory, f"{sanitized_title}-trailer.mp4")
    if os.path.exists(final_trailer_filename):
        return True

    def verify_title_match(video_title, show_title):
        video_title = video_title.lower()
        import re
        year_match = re.search(r'\((\d{4})\)', show_title)
        year = year_match.group(1) if year_match else None
        base_title = re.sub(r'\s*\(\d{4}\)\s*', '', show_title).lower().strip()
        if base_title in video_title:
            if year and year in video_title:
                return True
            elif not year:
                return True
        show_title_parts = show_title.lower().split(':')
        if all(part.strip() in video_title for part in show_title_parts):
            return True
        if show_title.lower() in video_title:
            return True
        if year and base_title in video_title and year in video_title:
            return True
        return False

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'noplaylist': True,
        'max_downloads': 1,
        'merge_output_format': 'mp4',
        'match_filter_func': short_videos_only,
        'default_search': 'ytsearch10',
        'extract_flat': 'in_playlist',
        'force_generic_extractor': False,
        'ignoreerrors': True,
        'quiet': not SHOW_YT_DLP_PROGRESS,
        'no_warnings': not SHOW_YT_DLP_PROGRESS,
    }
    # Use cookies_path resolved once at startup
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path
        print(f"Using cookies file: {cookies_path}")
    if YT_DLP_CUSTOM_OPTIONS:
        custom_opts = parse_ytdlp_options(YT_DLP_CUSTOM_OPTIONS)
        for key, value in custom_opts.items():
            if key == 'extractor_args' and key in ydl_opts:
                for service, args in value.items():
                    if service in ydl_opts['extractor_args']:
                        ydl_opts['extractor_args'][service].update(args)
                    else:
                        ydl_opts['extractor_args'][service] = args
            else:
                ydl_opts[key] = value

    if SHOW_YT_DLP_PROGRESS:
        print(f"Searching for trailer: {search_query}")
    else:
        print(f"Searching trailer for {show_title}...")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_query, download=False)
            if info and 'entries' in info:
                entries = list(filter(None, info['entries']))
                for video in entries:
                    if video:
                        duration = video.get('duration', 0)
                        video_title = video.get('title', '')
                        if SHOW_YT_DLP_PROGRESS:
                            print(f"Found video: {video_title} (Duration: {duration} seconds)")
                        if duration and duration <= 300:
                            if verify_title_match(video_title, show_title):
                                try:
                                    ydl.download([video['url']])
                                except yt_dlp.utils.DownloadError as e:
                                    if "has already been downloaded" in str(e):
                                        print_colored("Trailer already exists", 'green')
                                        return True
                                    if "Maximum number of downloads reached" in str(e):
                                        if os.path.exists(final_trailer_filename):
                                            print_colored(f"Trailer successfully downloaded for '{show_title}'", 'green')
                                            return True
                                    print(f"Failed to download video: {str(e)}")
                                    continue
                                if os.path.exists(final_trailer_filename):
                                    print_colored(f"Trailer successfully downloaded for '{show_title}'", 'green')
                                    return True
                            else:
                                if SHOW_YT_DLP_PROGRESS:
                                    print(f"Skipping video - title doesn't match show title")
                                continue
                        else:
                            if SHOW_YT_DLP_PROGRESS:
                                print(f"Skipping video - duration {duration} seconds exceeds 5-minute limit")
                            continue
            print("No suitable videos found matching criteria")
            return False
        except Exception as e:
            if os.path.exists(final_trailer_filename):
                print_colored("Trailer download successful", 'green')
                return True
            if SHOW_YT_DLP_PROGRESS:
                print(f"Unexpected error downloading trailer for '{show_title}': {str(e)}")
            else:
                print_colored("Trailer download failed. Turn on SHOW_YT_DLP_PROGRESS for more info", 'red')
            return False

# Main processing
start_time = datetime.now()

for library_config in TV_LIBRARIES:
    library_name = library_config['name']
    library_genres_to_skip = library_config.get('genres_to_skip', [])
    need_genres = bool(library_genres_to_skip)

    print_colored(f"\nChecking your {library_name} library for missing trailers", 'blue')
    tv_section = plex.library.section(library_name)
    if USE_LABELS:
        filters = {'and': [{'label!': 'MTDfP'}]}
        all_shows = tv_section.search(filters=filters)
        print_colored(f"Found {len(all_shows)} TV shows without MTDfP label", 'blue')
    else:
        all_shows = tv_section.all()
    total_shows = len(all_shows)

    for index, show in enumerate(all_shows, start=1):
        print(f"Checking show {index}/{total_shows}: {show.title}")

        # Only reload from Plex if genre filtering is required and genres aren't already loaded
        if need_genres and not show.genres:
            result = plex_call(show.reload, label=f"reload '{show.title}'")
            if result is None and not show.genres:
                print_colored(f"  Skipping '{show.title}' — could not reload from Plex", 'red')
                continue

        if need_genres:
            show_genres = [genre.tag.lower() for genre in (show.genres or [])]
            if any(skip_genre.lower() in show_genres for skip_genre in library_genres_to_skip):
                print(f"Skipping '{show.title}' (Genres match skip list: {', '.join(show_genres)})")
                shows_skipped.append(show.title)
                continue

        if CHECK_PLEX_PASS_TRAILERS:
            extras = plex_call(show.extras, label=f"extras '{show.title}'")
            if extras is None:
                print_colored(f"  Skipping '{show.title}' — could not fetch extras from Plex", 'red')
                continue
            trailers = [extra for extra in extras if extra.type == 'clip' and extra.subtype == 'trailer']
            already_has_trailer = bool(trailers)
        else:
            already_has_trailer = has_local_trailer(normalize_path_for_docker(show.locations[0]))

        if not already_has_trailer:
            if DOWNLOAD_TRAILERS:
                show_directory = normalize_path_for_docker(show.locations[0])
                success = download_trailer(show.title, show_directory)
                if success:
                    folder_name = os.path.basename(show_directory)
                    shows_with_downloaded_trailers[folder_name] = show
                    shows_download_errors.discard(show.title)
                    shows_missing_trailers.discard(show.title)
                    if USE_LABELS:
                        add_mtdfp_label(show)
                    # Refresh metadata immediately using the existing show reference
                    if REFRESH_METADATA:
                        try:
                            print(f"Refreshing metadata for '{show.title}'")
                            show.refresh()
                        except Exception as e:
                            print(f"Failed to refresh metadata for '{show.title}': {e}")
                else:
                    shows_download_errors.add(show.title)
                    shows_missing_trailers.add(show.title)
            else:
                shows_missing_trailers.add(show.title)
        else:
            if USE_LABELS:
                add_mtdfp_label(show, "already has trailer")

if shows_skipped:
    print("\n")
    print_colored("TV Shows skipped (Matching Genre):", 'yellow')
    for show in sorted(shows_skipped):
        print(show)

if shows_missing_trailers:
    print("\n")
    print_colored("TV Shows missing trailers:", 'red')
    for show in sorted(set(shows_missing_trailers) - set(shows_skipped)):
        print(show)

if shows_with_downloaded_trailers:
    print("\n")
    print_colored("TV Shows with successfully downloaded trailers:", 'green')
    for show_folder in sorted(shows_with_downloaded_trailers.keys()):
        print(show_folder)

if shows_download_errors:
    print("\n")
    print_colored("TV Shows with failed trailer downloads:", 'red')
    for show in sorted(set(shows_download_errors)):
        print(show)

if not shows_missing_trailers and not shows_download_errors and not shows_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)

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

VERSION= "2025.11.2601"

# Set up logging
logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logs", "Movies")
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

# Load configuration from config.yml
if IS_DOCKER:
    config_path = '/config/config.yml'
else:
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yml')

with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configuration variables
PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')

# Handle multiple libraries configuration
MOVIE_LIBRARIES = config.get('MOVIE_LIBRARIES', [])
if not MOVIE_LIBRARIES:
    MOVIE_LIBRARY_NAME = config.get('MOVIE_LIBRARY_NAME')
    MOVIE_GENRES_TO_SKIP = config.get('MOVIE_GENRES_TO_SKIP', [])
    if MOVIE_LIBRARY_NAME:
        MOVIE_LIBRARIES = [{"name": MOVIE_LIBRARY_NAME, "genres_to_skip": MOVIE_GENRES_TO_SKIP}]

DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
REFRESH_METADATA = config.get('REFRESH_METADATA')
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
    """Check for cookies.txt in the cookies subfolder"""
    if IS_DOCKER:
        cookies_folder = Path('/cookies')
    else:
        cookies_folder = Path(__file__).parent.parent / 'cookies'

    cookies_file = cookies_folder / 'cookies.txt'

    if cookies_file.exists() and cookies_file.is_file():
        return str(cookies_file)

    return None

def parse_ytdlp_options(options_list):
    """Parse command-line style yt-dlp options into a dictionary."""
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

# Check for cookies file
cookies_path = get_cookies_path()
if cookies_path:
    print(f"{GREEN}Found cookies file: {cookies_path}{RESET}")

# Print configuration settings
print("\nConfiguration for this run:")
print(f"MOVIE_LIBRARIES: {[lib['name'] for lib in MOVIE_LIBRARIES]}")
for library in MOVIE_LIBRARIES:
    genres_to_skip = library.get('genres_to_skip', [])
    print(f"  {library['name']} - GENRES_TO_SKIP: {', '.join(genres_to_skip)}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"USE_LABELS: {GREEN}true{RESET}" if USE_LABELS else f"USE_LABELS: {ORANGE}false{RESET}")
print(f"PLEX_RETRY_ATTEMPTS: {PLEX_RETRY_ATTEMPTS}, PLEX_RETRY_DELAY: {PLEX_RETRY_DELAY}s")
if YT_DLP_CUSTOM_OPTIONS:
    print(f"YT_DLP_CUSTOM_OPTIONS: {', '.join(YT_DLP_CUSTOM_OPTIONS)}")
if IS_DOCKER:
    print(f"Running in: {GREEN}Docker Container{RESET}")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Lists to store movie trailer status
movies_with_downloaded_trailers = {}
movies_download_errors = []
movies_skipped = []
movies_missing_trailers = []

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

def add_mtdfp_label(movie, context=""):
    try:
        movie.edit(**{'label.locked': 0})
        existing_labels = [label.tag for label in (movie.labels or [])]
        if 'MTDfP' not in existing_labels:
            movie.addLabel('MTDfP')
            context_text = f" ({context})" if context else ""
            print_colored(f"Added MTDfP label to '{movie.title}'{context_text}", 'green')
        else:
            print_colored(f"Movie '{movie.title}' already has MTDfP label", 'blue')
    except Exception as e:
        print_colored(f"Failed to add MTDfP label to '{movie.title}': {e}", 'red')

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

def cleanup_trailer_files(movie_title, movie_year, trailers_folder):
    for file in os.listdir(trailers_folder):
        if file.startswith(f"{movie_title} ({movie_year})-trailer.") and not file.endswith(".mp4"):
            try:
                os.remove(os.path.join(trailers_folder, file))
            except OSError as e:
                print(f"Failed to delete {file}: {e}")

def has_local_trailer(movie_path):
    movie_folder = os.path.dirname(movie_path)
    if not os.path.isdir(movie_folder):
        print(f"Warning: Cannot access directory: {movie_folder}")
        return False
    try:
        folder_contents = os.listdir(movie_folder)
    except OSError as e:
        print(f"Warning: Error listing directory '{movie_folder}': {e}")
        return False
    for f in folder_contents:
        lower_f = f.lower()
        if lower_f.endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            name_without_ext, _ = os.path.splitext(lower_f)
            if name_without_ext.endswith("-trailer"):
                return True
    trailers_subfolder = os.path.join(movie_folder, "Trailers")
    if os.path.isdir(trailers_subfolder):
        try:
            subfolder_contents = os.listdir(trailers_subfolder)
        except OSError as e:
            print(f"Warning: Error listing directory '{trailers_subfolder}': {e}")
            return False
        for sub_f in subfolder_contents:
            if sub_f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
                return True
    return False

def download_trailer(movie_title, movie_year, movie_path):
    sanitized_title = movie_title.replace(":", " -")
    key_terms = movie_title.lower().split(":")
    main_title = key_terms[0].strip()
    subtitle = key_terms[1].strip() if len(key_terms) > 1 else None
    search_query = f"ytsearch10:{movie_title} {movie_year} official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"
    movie_folder = os.path.dirname(movie_path)
    trailers_folder = os.path.join(movie_folder, "Trailers")
    os.makedirs(trailers_folder, exist_ok=True)
    output_filename = os.path.join(trailers_folder, f"{sanitized_title} ({movie_year})-trailer.%(ext)s")
    final_trailer_filename = os.path.join(trailers_folder, f"{sanitized_title} ({movie_year})-trailer.mp4")
    if os.path.exists(final_trailer_filename):
        return True
    cookies_path = get_cookies_path()
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

    def verify_title_match(video_title, movie_title, year):
        video_title = video_title.lower()
        movie_title_lower = movie_title.lower()
        movie_title_parts = movie_title_lower.split(':')
        year_str = str(year)
        if year_str in video_title and movie_title_lower in video_title:
            return True
        if all(part.strip() in video_title for part in movie_title_parts) and year_str in video_title:
            return True
        import re
        sanitized_movie_title = re.sub(r'[^\w\s]', '', movie_title_lower).strip()
        sanitized_video_title = re.sub(r'[^\w\s]', '', video_title).strip()
        if sanitized_movie_title in sanitized_video_title and year_str in video_title:
            return True
        if len(movie_title_lower) > 20:
            partial_title = movie_title_lower[:int(len(movie_title_lower) * 0.7)]
            if partial_title in video_title and year_str in video_title:
                return True
        if movie_title_lower in video_title:
            return True
        return False

    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching for trailer: {search_query}")
            try:
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    entries = list(filter(None, info['entries']))
                    for video in entries:
                        if video:
                            duration = video.get('duration', 0)
                            video_title = video.get('title', '')
                            print(f"Found video: {video_title} (Duration: {duration} seconds)")
                            if duration and duration <= 300:
                                if verify_title_match(video_title, movie_title, movie_year):
                                    try:
                                        ydl.download([video['url']])
                                    except yt_dlp.utils.DownloadError as e:
                                        if "has already been downloaded" in str(e):
                                            print("Trailer already exists")
                                            return True
                                        if "Maximum number of downloads reached" in str(e):
                                            if os.path.exists(final_trailer_filename):
                                                print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
                                                return True
                                        print(f"Failed to download video: {str(e)}")
                                        continue
                                    if os.path.exists(final_trailer_filename):
                                        print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
                                        return True
                                else:
                                    print(f"Skipping video - title doesn't match movie title")
                                    continue
                            else:
                                print(f"Skipping video - duration {duration} seconds exceeds 5-minute limit")
                                continue
                    print("No suitable videos found matching criteria")
                    return False
            except Exception as e:
                if os.path.exists(final_trailer_filename):
                    print(f"Trailer exists despite error: {str(e)}")
                    return True
                print(f"Unexpected error downloading trailer for '{movie_title} ({movie_year})': {str(e)}")
                return False
    else:
        print(f"Searching trailer for {movie_title} ({movie_year})...")
        ydl_opts['quiet'] = True
        ydl_opts['no_warnings'] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    entries = list(filter(None, info['entries']))
                    for video in entries:
                        if video:
                            duration = video.get('duration', 0)
                            if duration <= 300 and verify_title_match(video.get('title', ''), movie_title, movie_year):
                                try:
                                    ydl.download([video['url']])
                                except yt_dlp.utils.DownloadError as e:
                                    if "has already been downloaded" in str(e):
                                        print_colored("Trailer already exists", 'green')
                                        return True
                                    if "Maximum number of downloads reached" in str(e):
                                        if os.path.exists(final_trailer_filename):
                                            print_colored("Trailer download successful", 'green')
                                            return True
                                    continue
                                if os.path.exists(final_trailer_filename):
                                    print_colored("Trailer download successful", 'green')
                                    return True
                return False
            except Exception as e:
                if os.path.exists(final_trailer_filename):
                    print_colored("Trailer download successful", 'green')
                    return True
                print_colored("Trailer download failed. Turn on SHOW_YT_DLP_PROGRESS for more info", 'red')
                return False

    cleanup_trailer_files(sanitized_title, movie_year, trailers_folder)
    return False

# Main processing
start_time = datetime.now()

for library_config in MOVIE_LIBRARIES:
    library_name = library_config['name']
    library_genres_to_skip = library_config.get('genres_to_skip', [])
    print_colored(f"\nChecking your {library_name} library for missing trailers", 'blue')
    if USE_LABELS:
        filters = {'and': [{'label!': 'MTDfP'}]}
        all_movies = plex.library.section(library_name).search(filters=filters)
        print_colored(f"Found {len(all_movies)} movies without MTDfP label", 'blue')
    else:
        all_movies = plex.library.section(library_name).all()
    total_movies = len(all_movies)
    for index, movie in enumerate(all_movies, start=1):
        print(f"Checking movie {index}/{total_movies}: {movie.title}")

        result = plex_call(movie.reload, label=f"reload '{movie.title}'")
        if result is None and not hasattr(movie, 'genres'):
            print_colored(f"  Skipping '{movie.title}' — could not reload from Plex", 'red')
            continue

        movie_genres = [genre.tag.lower() for genre in (movie.genres or [])]
        if any(skip_genre.lower() in movie_genres for skip_genre in library_genres_to_skip):
            print(f"Skipping '{movie.title}' (Genres match skip list: {', '.join(movie_genres)})")
            movies_skipped.append((movie.title, movie.year))
            continue

        if CHECK_PLEX_PASS_TRAILERS:
            extras = plex_call(movie.extras, label=f"extras '{movie.title}'")
            if extras is None:
                print_colored(f"  Skipping '{movie.title}' — could not fetch extras from Plex", 'red')
                continue
            trailers = [extra for extra in extras if extra.type == 'clip' and extra.subtype == 'trailer']
            already_has_trailer = bool(trailers)
        else:
            already_has_trailer = has_local_trailer(normalize_path_for_docker(movie.locations[0]))

        if not already_has_trailer:
            if DOWNLOAD_TRAILERS:
                movie_path = normalize_path_for_docker(movie.locations[0])
                success = download_trailer(movie.title, movie.year, movie_path)
                if success:
                    movies_with_downloaded_trailers[(movie.title, movie.year)] = movie.ratingKey
                    if (movie.title, movie.year) in movies_download_errors:
                        movies_download_errors.remove((movie.title, movie.year))
                    if (movie.title, movie.year) in movies_missing_trailers:
                        movies_missing_trailers.remove((movie.title, movie.year))
                    if USE_LABELS:
                        add_mtdfp_label(movie)
                else:
                    if (movie.title, movie.year) not in movies_download_errors:
                        movies_download_errors.append((movie.title, movie.year))
                    if (movie.title, movie.year) not in movies_missing_trailers:
                        movies_missing_trailers.append((movie.title, movie.year))
            else:
                movies_missing_trailers.append((movie.title, movie.year))
        else:
            if USE_LABELS:
                add_mtdfp_label(movie, "already has trailer")

if movies_skipped:
    print("\n")
    print_colored("Movies skipped (Matching Genre):", 'yellow')
    for title, year in sorted(movies_skipped):
        print(f"{title} ({year})")

if movies_missing_trailers:
    print("\n")
    print_colored("Movies missing trailers:", 'red')
    for title, year in sorted(movies_missing_trailers):
        print(f"{title} ({year})")

if movies_with_downloaded_trailers:
    print("\n")
    print_colored("Movies with successfully downloaded trailers:", 'green')
    for title, year in sorted(movies_with_downloaded_trailers.keys()):
        print(f"{title} ({year})")

if REFRESH_METADATA and movies_with_downloaded_trailers:
    print_colored("\nRefreshing metadata for movies with new trailers:", 'blue')
    for (title, year), rating_key in movies_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"Failed to refresh metadata for '{title} ({year})': {e}")

if movies_download_errors:
    print("\n")
    print_colored("Movies with failed trailer downloads:", 'red')
    for title, year in sorted(movies_download_errors):
        print(f"{title} ({year})")

if not movies_missing_trailers and not movies_download_errors and not movies_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)

import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime

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

# --- Configuration ---
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yml')
with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')
TV_LIBRARY_NAME = config.get('TV_LIBRARY_NAME')
REFRESH_METADATA = config.get('REFRESH_METADATA')
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
TV_GENRES_TO_SKIP = config.get('TV_GENRES_TO_SKIP', [])
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)

# Print configuration
print("\nConfiguration for this run:")
print(f"TV_LIBRARY_NAME: {TV_LIBRARY_NAME}")
print(f"TV_GENRES_TO_SKIP: {', '.join(TV_GENRES_TO_SKIP)}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Lists to store the status of trailer downloads
shows_with_downloaded_trailers = {}
shows_download_errors = []
shows_skipped = []
shows_missing_trailers = []

def short_videos_only(info_dict, incomplete=False):
    """
    A match-filter function for yt-dlp that rejects videos over 5 minutes (300 seconds).
    Return None if acceptable; return a string (reason) if the video should be skipped.
    """
    duration = info_dict.get('duration')
    if duration and duration > 300:
        return f"Skipping video because it's too long ({duration} seconds)."
    return None

def check_download_success(d):
    """
    Called after each chunk or when download is complete.
    We only mark the trailer as 'downloaded' if the status is 'finished'.
    """
    if d['status'] == 'finished':
        # Extract the 'show folder' name from the path for logging
        trailer_path = os.path.dirname(d['filename'])
        show_folder = os.path.basename(os.path.dirname(trailer_path))
        print(f"[download] 100% of {d['filename']}")
        # Store the downloaded trailer marker
        shows_with_downloaded_trailers[show_folder] = None

def download_trailer(show_title, show_directory):
    # Build search query
    search_query = f"{show_title} TV show trailer"
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_query)}"

    # Ensure 'Trailers' subfolder exists
    trailers_directory = os.path.join(show_directory, 'Trailers')
    os.makedirs(trailers_directory, exist_ok=True)

    # Prepare the output template (filename)
    output_filename = os.path.join(trailers_directory, f"{show_title}-trailer.%(ext)s")

    # If there's already a trailer file, skip
    if any(fname.endswith(('.mp4', '.mkv'))
           for fname in os.listdir(trailers_directory)
           if '-trailer' in fname):
        return False  # No need to redownload

    # yt-dlp options, including match_filter for short videos
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'noplaylist': True,
        'max_downloads': 1,
        'progress_hooks': [check_download_success],
        'merge_output_format': 'mp4',
        'match_filter_func': short_videos_only
    }

    # Download logic
    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching for trailer: {search_url}")
            try:
                ydl.download([search_url])
            except yt_dlp.utils.MaxDownloadsReached:
                # Means the first valid video was downloaded
                print(f"Trailer successfully downloaded for '{show_title}'")
            except Exception as e:
                print(f"Failed to download trailer for '{show_title}': {e}")
                return False
    else:
        print(f"Searching trailer for {show_title}...")
        ydl_opts['quiet'] = True
        ydl_opts['no_warnings'] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.download([search_url])
                print_colored("Trailer download successful", 'green')
            except yt_dlp.utils.MaxDownloadsReached:
                print_colored("Trailer download successful", 'green')
            except Exception:
                print_colored("Trailer download failed. Turn on SHOW_YT_DLP_PROGRESS for more info", 'red')
                return False

    return True

# Main processing
start_time = datetime.now()
print_colored(f"\nChecking your {TV_LIBRARY_NAME} library for missing trailers", 'blue')

all_shows = plex.library.section(TV_LIBRARY_NAME).all()
total_shows = len(all_shows)

for index, show in enumerate(all_shows, start=1):
    print(f"Checking show {index}/{total_shows}: {show.title}")
    show.reload()

    # Skip if show has any of the 'skip' genres
    show_genres = [genre.tag.lower() for genre in show.genres]
    if any(skip_genre.lower() in show_genres for skip_genre in TV_GENRES_TO_SKIP):
        print(f"Skipping '{show.title}' (Genres: {', '.join(genre.tag for genre in show.genres)} matching skip list)")
        shows_skipped.append(show.title)
        continue

    # Check for existing trailers in Plex (Extras)
    trailers = [extra for extra in show.extras() if extra.type == 'clip' and extra.subtype == 'trailer']
    if not trailers:
        # If no trailer is found, decide whether to download
        if DOWNLOAD_TRAILERS:
            show_directory = show.locations[0]
            success = download_trailer(show.title, show_directory)
            if success:
                # Mark as downloaded (see check_download_success hook too)
                shows_with_downloaded_trailers[os.path.basename(show_directory)] = show.ratingKey
            else:
                # Download attempt failed
                shows_download_errors.append(show.title)
        else:
            # If we're not downloading, just record it as missing
            shows_missing_trailers.append(show.title)

if shows_skipped:
    print("\n")
    print_colored("TV Shows skipped (Matching Genre):", 'yellow')
    for show in sorted(shows_skipped):
        print(show)

if shows_missing_trailers:
    print("\n")
    print_colored("TV Shows missing trailers:", 'red')
    # Exclude any that might have been skipped due to genre
    for show in sorted(set(shows_missing_trailers) - set(shows_skipped)):
        print(show)

if shows_with_downloaded_trailers:
    print("\n")
    print_colored("TV Shows with successfully downloaded trailers:", 'green')
    for show_folder in sorted(shows_with_downloaded_trailers.keys()):
        print(show_folder)

if REFRESH_METADATA and shows_with_downloaded_trailers:
    print_colored("\nRefreshing metadata for TV shows with new trailers:", 'blue')
    for show_folder, rating_key in shows_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"Failed to refresh metadata for '{show_folder}': {e}")

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

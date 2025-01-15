import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime

# Set up logging
logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logs", "Movies")
os.makedirs(logs_dir, exist_ok=True)
log_file = os.path.join(logs_dir, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

class Logger:
    def __init__(self, log_file):
        self.terminal = sys.stdout
        self.log = open(log_file, "a", encoding="utf-8")  # Specify UTF-8 encoding

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

# Load configuration from config.yml
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yml')
with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configuration variables
PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')
MOVIE_LIBRARY_NAME = config.get('MOVIE_LIBRARY_NAME')
REFRESH_METADATA = config.get('REFRESH_METADATA')
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
MOVIE_GENRES_TO_SKIP = config.get('MOVIE_GENRES_TO_SKIP', [])

# Print configuration settings
print("\nConfiguration for this run:")
print(f"MOVIE_LIBRARY_NAME: {MOVIE_LIBRARY_NAME}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"MOVIE_GENRES_TO_SKIP: {', '.join(MOVIE_GENRES_TO_SKIP)}")

# Connect to the Plex server
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Lists to store movies' status
movies_with_downloaded_trailers = {}
movies_download_errors = []
movies_skipped = []
movies_missing_trailers = []

def print_colored(text, color, end="\n"):
    colors = {'red': RED, 'green': GREEN, 'blue': BLUE, 'yellow': ORANGE, 'white': RESET}
    print(f"{colors.get(color, RESET)}{text}{RESET}", end=end)

def cleanup_trailer_files(movie_title, movie_directory):
    output_directory = os.path.dirname(movie_directory)
    for file in os.listdir(output_directory):
        if file.startswith(f"{movie_title}-trailer.") and not file.endswith(".mp4"):
            try:
                os.remove(os.path.join(output_directory, file))
            except OSError as e:
                print(f"Failed to delete {file}: {e}")

def download_trailer(movie_title, movie_directory):
    search_query = f"{movie_title} movie trailer"
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_query)}"
    output_filename = os.path.join(os.path.dirname(movie_directory), f"{movie_title}-trailer.%(ext)s")
    final_trailer_filename = os.path.join(os.path.dirname(movie_directory), f"{movie_title}-trailer.mp4")

    if os.path.exists(final_trailer_filename):
        return False

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'noplaylist': True,
        'max_downloads': 1,
        'merge_output_format': 'mp4',
    }

    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching for trailer: {search_url}")
            try:
                ydl.download([search_url])
            except yt_dlp.utils.MaxDownloadsReached:
                print(f"Trailer successfully downloaded for '{movie_title}'")
            except Exception as e:
                print(f"Failed to download trailer for '{movie_title}': {e}")
                return False
    else:
        print(f"Attempting Trailer download for {movie_title}")
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

    cleanup_trailer_files(movie_title, movie_directory)
    return True

# Main processing
start_time = datetime.now()
print_colored(f"\nChecking your {MOVIE_LIBRARY_NAME} library for missing trailers", 'blue')
total_movies = len(plex.library.section(MOVIE_LIBRARY_NAME).all())
for index, movie in enumerate(plex.library.section(MOVIE_LIBRARY_NAME).all(), start=1):
    print(f"Checking movie {index}/{total_movies}: {movie.title}")
    movie.reload()

    movie_genres = [genre.tag.lower() for genre in movie.genres]
    if any(skip_genre.lower() in movie_genres for skip_genre in MOVIE_GENRES_TO_SKIP):
        print(f"Skipping '{movie.title}' (Genres: {', '.join(genre.tag for genre in movie.genres)} matching skip list)")
        movies_skipped.append((movie.title, movie.year))
        continue

    trailers = [extra for extra in movie.extras() if extra.type == 'clip' and extra.subtype == 'trailer']
    if not trailers:
        movie_directory = movie.locations[0]
        if DOWNLOAD_TRAILERS:
            if download_trailer(movie.title, movie_directory):
                movies_with_downloaded_trailers[(movie.title, movie.year)] = movie.ratingKey
            else:
                movies_download_errors.append((movie.title, movie.year))
        else:
            movies_missing_trailers.append((movie.title, movie.year))

# Refresh metadata for movies with downloaded trailers
if REFRESH_METADATA and movies_with_downloaded_trailers:
    print("\nRefreshing metadata for movies with new trailers...\n")
    for movie, rating_key in movies_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"Failed to refresh metadata for '{movie[0]} ({movie[1]})': {e}")

# Print the results
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

if movies_download_errors:
    print("\n")
    print_colored("Movies with failed trailer downloads:", 'red')
    for title, year in sorted(movies_download_errors):
        print(f"{title} ({year})")

if not movies_missing_trailers and not movies_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)

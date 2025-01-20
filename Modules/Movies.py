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

def print_colored(text, color, end="\n"):
    colors = {'red': RED, 'green': GREEN, 'blue': BLUE, 'yellow': ORANGE, 'white': RESET}
    print(f"{colors.get(color, RESET)}{text}{RESET}", end=end)

# Load configuration from config.yml
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yml')
with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configuration variables
PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')
MOVIE_LIBRARY_NAME = config.get('MOVIE_LIBRARY_NAME')
MOVIE_GENRES_TO_SKIP = config.get('MOVIE_GENRES_TO_SKIP', [])
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
REFRESH_METADATA = config.get('REFRESH_METADATA')
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
CHECK_LOCAL_TRAILERS_ONLY = config.get('CHECK_LOCAL_TRAILERS_ONLY', False)

# Print configuration settings
print("\nConfiguration for this run:")
print(f"MOVIE_LIBRARY_NAME: {MOVIE_LIBRARY_NAME}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"MOVIE_GENRES_TO_SKIP: {', '.join(MOVIE_GENRES_TO_SKIP)}")
print(f"CHECK_LOCAL_TRAILERS_ONLY: {GREEN}true{RESET}" if CHECK_LOCAL_TRAILERS_ONLY else f"CHECK_LOCAL_TRAILERS_ONLY: {ORANGE}false{RESET}")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Lists to store movie trailer status
movies_with_downloaded_trailers = {}
movies_download_errors = []
movies_skipped = []
movies_missing_trailers = []

def short_videos_only(info_dict, incomplete=False):
    """
    A match-filter function for yt-dlp that rejects videos over 5 minutes (300 seconds).
    Return None if the video is acceptable; return a reason (string) if it should be skipped.
    """
    duration = info_dict.get('duration')
    if duration and duration > 300:
        return f"Skipping video because it's too long ({duration} seconds)."
    return None

def cleanup_trailer_files(movie_title, movie_year, trailers_folder):
    """
    Remove any leftover partial download files that match our trailer filename prefix
    (excluding the final .mp4).
    """
    for file in os.listdir(trailers_folder):
        if file.startswith(f"{movie_title} ({movie_year})-trailer.") and not file.endswith(".mp4"):
            try:
                os.remove(os.path.join(trailers_folder, file))
            except OSError as e:
                print(f"Failed to delete {file}: {e}")

def has_local_trailer(movie_path):
    """
    Check the local filesystem for an existing trailer file.
    Conditions:
      1) A file in the same folder ending with "-trailer" before its extension.
      2) A subfolder named "Trailers" containing at least one video file.
    """
    movie_folder = os.path.dirname(movie_path)

    # 1) Look for files named "...-trailer.ext"
    for f in os.listdir(movie_folder):
        # Convert everything to lowercase for safer checking
        lower_f = f.lower()
        # We'll allow a few common video extensions. You can expand this as needed.
        if lower_f.endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            # Now check if the part before the extension ends with "-trailer"
            name_without_ext, _ = os.path.splitext(lower_f)
            if name_without_ext.endswith("-trailer"):
                return True

    # 2) Look for subfolder "Trailers" with at least one video file
    trailers_subfolder = os.path.join(movie_folder, "Trailers")
    if os.path.isdir(trailers_subfolder):
        for sub_f in os.listdir(trailers_subfolder):
            if sub_f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
                return True

    # If neither condition is met, there's no local trailer
    return False

def download_trailer(movie_title, movie_year, movie_path):
    """
    Attempt to download a trailer for the given movie using a YouTube search.
    Trailers are saved in a 'Trailers' subfolder with the name:
    '{movie_title} ({movie_year})-trailer.mp4'.
    """
    # Prepare the base search query
    search_query = f"{movie_title} movie trailer"

    # If PREFERRED_LANGUAGE is not "original", add it to the query
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"

    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_query)}"

    # Build the output path in a 'Trailers' subfolder
    movie_folder = os.path.dirname(movie_path)
    trailers_folder = os.path.join(movie_folder, "Trailers")
    os.makedirs(trailers_folder, exist_ok=True)

    # Our desired final trailer filename
    output_filename = os.path.join(
        trailers_folder,
        f"{movie_title} ({movie_year})-trailer.%(ext)s"
    )
    final_trailer_filename = os.path.join(
        trailers_folder,
        f"{movie_title} ({movie_year})-trailer.mp4"
    )

    # If a trailer file already exists, no need to download again
    if os.path.exists(final_trailer_filename):
        return False

    # yt-dlp options
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'noplaylist': True,
        'max_downloads': 1,
        'merge_output_format': 'mp4',
        # Only allow short videos (< 5 min)
        'match_filter_func': short_videos_only
    }

    # Download logic
    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching for trailer: {search_url}")
            try:
                ydl.download([search_url])
                # If it doesn't raise MaxDownloadsReached, it means it finished or found no suitable video
                print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
            except yt_dlp.utils.MaxDownloadsReached:
                # This means the first valid video got downloaded
                print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
            except Exception as e:
                print(f"Failed to download trailer for '{movie_title} ({movie_year})': {e}")
                return False
    else:
        print(f"Searching trailer for {movie_title} ({movie_year})...")
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

    # Clean up leftover partial files
    cleanup_trailer_files(movie_title, movie_year, trailers_folder)

    # If the expected mp4 doesn't exist, something went wrong
    if not os.path.exists(final_trailer_filename):
        return False

    return True

# Main processing
start_time = datetime.now()
print_colored(f"\nChecking your {MOVIE_LIBRARY_NAME} library for missing trailers", 'blue')
all_movies = plex.library.section(MOVIE_LIBRARY_NAME).all()
total_movies = len(all_movies)

for index, movie in enumerate(all_movies, start=1):
    print(f"Checking movie {index}/{total_movies}: {movie.title}")
    movie.reload()

    # If it has any skip-genres, skip it
    movie_genres = [genre.tag.lower() for genre in (movie.genres or [])]
    if any(skip_genre.lower() in movie_genres for skip_genre in MOVIE_GENRES_TO_SKIP):
        print(f"Skipping '{movie.title}' (Genres match skip list: {', '.join(movie_genres)})")
        movies_skipped.append((movie.title, movie.year))
        continue

    # Decide how to check for an existing trailer
    if CHECK_LOCAL_TRAILERS_ONLY:
        # Check for local trailer files only
        already_has_trailer = has_local_trailer(movie.locations[0])
    else:
        # Check Plex extras for an existing trailer subtype
        trailers = [
            extra
            for extra in movie.extras()
            if extra.type == 'clip' and extra.subtype == 'trailer'
        ]
        already_has_trailer = bool(trailers)

    if not already_has_trailer:
        # No trailer found
        if DOWNLOAD_TRAILERS:
            movie_path = movie.locations[0]
            success = download_trailer(movie.title, movie.year, movie_path)
            if success:
                # Save ratingKey so we can refresh later
                movies_with_downloaded_trailers[(movie.title, movie.year)] = movie.ratingKey
            else:
                movies_download_errors.append((movie.title, movie.year))
                # Still no trailer after attempting download
                movies_missing_trailers.append((movie.title, movie.year))
        else:
            # If we are not downloading, just note it's missing
            movies_missing_trailers.append((movie.title, movie.year))

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

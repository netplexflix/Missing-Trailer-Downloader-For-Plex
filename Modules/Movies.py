import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime

VERSION= "2025.10.05"

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
CHECK_PLEX_PASS_TRAILERS = config.get('CHECK_PLEX_PASS_TRAILERS', True)
MAP_PATH = config.get('MAP_PATH', False)
PATH_MAPPINGS = config.get('PATH_MAPPINGS', {})
USE_LABELS = config.get('USE_LABELS', False)

# Print configuration settings
print("\nConfiguration for this run:")
print(f"MOVIE_LIBRARY_NAME: {MOVIE_LIBRARY_NAME}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"MOVIE_GENRES_TO_SKIP: {', '.join(MOVIE_GENRES_TO_SKIP)}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"MAP_PATH: {GREEN}true{RESET}" if MAP_PATH else f"MAP_PATH: {ORANGE}false{RESET}")
print(f"USE_LABELS: {GREEN}true{RESET}" if USE_LABELS else f"USE_LABELS: {ORANGE}false{RESET}")

if MAP_PATH:
    print("PATH_MAPPINGS:")
    for src, dst in PATH_MAPPINGS.items():
        print(f"  '{src}' => '{dst}'")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# -------------------------------------------------------------------
# HELPER FUNCTION: Map a Plex path to a local path if needed
# -------------------------------------------------------------------
def map_path_if_needed(original_path):
    """
    If MAP_PATH is True, replace any matching prefix from PATH_MAPPINGS
    with its mapped value. Otherwise, return the path as-is.
    """
    if not MAP_PATH or not PATH_MAPPINGS:
        return original_path

    # Sort the mappings by length so that longer matches occur first
    # (This helps in case you have nested paths in PATH_MAPPINGS)
    sorted_mappings = sorted(PATH_MAPPINGS.items(), key=lambda x: len(x[0]), reverse=True)
    for source_prefix, dest_prefix in sorted_mappings:
        # If the original path starts with source_prefix, replace it
        if original_path.startswith(source_prefix):
            # Replace only once from the start
            mapped_path = original_path.replace(source_prefix, dest_prefix, 1)
            # Print a debug line for clarity
            print(f"Mapping path: '{original_path}' => '{mapped_path}'")
            return mapped_path

    return original_path

# Lists to store movie trailer status
movies_with_downloaded_trailers = {}
movies_download_errors = []
movies_skipped = []
movies_missing_trailers = []

def short_videos_only(info_dict, incomplete=False):
    """
    A match-filter function for yt-dlp that rejects videos over 5 minutes (300 seconds).
    Returns None if the video is acceptable; returns a reason (string) if it should be skipped.
    """
    duration = info_dict.get('duration')
    
    # Add debug logging
    print(f"Video duration check - Title: {info_dict.get('title', 'Unknown')}")
    print(f"Duration: {duration if duration is not None else 'Not available'} seconds")
    
    if duration is None:
        print("Warning: Could not determine video duration before download")
        # You can choose to either skip videos with unknown duration
        # return "Skipping video with unknown duration"
        # Or let them through (current behavior)
        return None
        
    if duration > 300:
        print(f"Rejecting video: Duration {duration} seconds exceeds 5 minute limit")
        return f"Skipping video because it's too long ({duration} seconds)"
        
    print(f"Accepting video: Duration {duration} seconds is within 5 minute limit")
    return None

def add_mtdfp_label(movie, context=""):
    """
    Add MTDfP label to a movie if it doesn't already have it.
    Only called when USE_LABELS is True.
    
    Args:
        movie: The movie object to add the label to
        context: Optional context string for logging (e.g., "already has trailer")
    """
    try:
        # First unlock the labels field
        movie.edit(**{'label.locked': 0})
        
        # Check if MTDfP label already exists
        existing_labels = [label.tag for label in (movie.labels or [])]
        if 'MTDfP' not in existing_labels:
            # Use addLabel method which works
            movie.addLabel('MTDfP')
            context_text = f" ({context})" if context else ""
            print_colored(f"Added MTDfP label to '{movie.title}'{context_text}", 'green')
        else:
            print_colored(f"Movie '{movie.title}' already has MTDfP label", 'blue')
    except Exception as e:
        print_colored(f"Failed to add MTDfP label to '{movie.title}': {e}", 'red')

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
    # Apply path mapping first
    mapped_path = map_path_if_needed(movie_path)
    movie_folder = os.path.dirname(mapped_path)

    # If the folder doesn't exist or is inaccessible, return False
    if not os.path.isdir(movie_folder):
        # You could handle or log a warning here
        print(f"Warning: Cannot access directory: {movie_folder}")
        return False

    try:
        folder_contents = os.listdir(movie_folder)
    except OSError as e:
        print(f"Warning: Error listing directory '{movie_folder}': {e}")
        return False

    # 1) Look for files named "...-trailer.ext"
    for f in folder_contents:
        lower_f = f.lower()
        if lower_f.endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            name_without_ext, _ = os.path.splitext(lower_f)
            if name_without_ext.endswith("-trailer"):
                return True

    # 2) Look for subfolder "Trailers" with at least one video file
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
    """
    Attempt to download a trailer for the given movie using a YouTube search.
    Trailers are saved in a 'Trailers' subfolder with the name:
    '{movie_title} ({movie_year})-trailer.mp4'.
    """
    # Sanitize movie_title to remove or replace problematic characters
    sanitized_title = movie_title.replace(":", " -")

    # Extract key terms from movie title (for title matching)
    key_terms = movie_title.lower().split(":")
    main_title = key_terms[0].strip()
    subtitle = key_terms[1].strip() if len(key_terms) > 1 else None

    # Prepare the search query with year for better accuracy
    search_query = f"ytsearch10:{movie_title} {movie_year} official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"

    # Apply path mapping before building the 'Trailers' subfolder
    mapped_movie_path = map_path_if_needed(movie_path)
    movie_folder = os.path.dirname(mapped_movie_path)
    trailers_folder = os.path.join(movie_folder, "Trailers")

    # Make sure the folder exists
    os.makedirs(trailers_folder, exist_ok=True)

    output_filename = os.path.join(
        trailers_folder,
        f"{sanitized_title} ({movie_year})-trailer.%(ext)s"
    )
    final_trailer_filename = os.path.join(
        trailers_folder,
        f"{sanitized_title} ({movie_year})-trailer.mp4"
    )

    # If a trailer file already exists, no need to download again
    if os.path.exists(final_trailer_filename):
        return True

    # Get cookies configuration from config if available
    cookies_from_browser = config.get('YT_DLP_COOKIES_FROM_BROWSER', None)
    cookies_file = config.get('YT_DLP_COOKIES_FILE', None)

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
        'no_warnings': not SHOW_YT_DLP_PROGRESS
    }
    
    # Add cookies options if configured
    if cookies_from_browser:
        ydl_opts['cookies_from_browser'] = cookies_from_browser
        print(f"Using cookies from browser: {cookies_from_browser}")
    elif cookies_file:
        ydl_opts['cookies'] = cookies_file
        print(f"Using cookies file: {cookies_file}")

    def verify_title_match(video_title, movie_title, year):
        """
        Verify that the video title is a valid match for the movie.
        Improved to better handle movie titles and year matching.
        """
        video_title = video_title.lower()
        movie_title_lower = movie_title.lower()
        movie_title_parts = movie_title_lower.split(':')
        year_str = str(year)
        
        # 1. Check if year and full title match
        if year_str in video_title and movie_title_lower in video_title:
            return True
            
        # 2. Check if all parts of a title with colons are present
        if all(part.strip() in video_title for part in movie_title_parts) and year_str in video_title:
            return True
            
        # 3. Handle titles with special characters - remove them for comparison
        import re
        sanitized_movie_title = re.sub(r'[^\w\s]', '', movie_title_lower).strip()
        sanitized_video_title = re.sub(r'[^\w\s]', '', video_title).strip()
        
        if sanitized_movie_title in sanitized_video_title and year_str in video_title:
            return True
            
        # 4. For longer titles, check if a substantial portion matches
        if len(movie_title_lower) > 20:
            # Get first 70% of the title
            partial_title = movie_title_lower[:int(len(movie_title_lower) * 0.7)]
            if partial_title in video_title and year_str in video_title:
                return True
                
        # 5. Original checks for backward compatibility
        if movie_title_lower in video_title:
            return True
                
        return False

    # Download logic
    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Searching for trailer: {search_query}")
            try:
                # Extract info first to check duration
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    entries = list(filter(None, info['entries']))
                    
                    # Try each entry until we find one that meets our criteria
                    for video in entries:
                        if video:
                            duration = video.get('duration', 0)
                            video_title = video.get('title', '')
                            print(f"Found video: {video_title} (Duration: {duration} seconds)")
                            
                            # Check both duration and title match
                            if duration and duration <= 300:
                                if verify_title_match(video_title, movie_title, movie_year):
                                    try:
                                        ydl.download([video['url']])
                                    except yt_dlp.utils.DownloadError as e:
                                        if "has already been downloaded" in str(e):
                                            print("Trailer already exists")
                                            return True
                                        if "Maximum number of downloads reached" in str(e):
                                            # Check if the file exists despite the max downloads message
                                            if os.path.exists(final_trailer_filename):
                                                print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
                                                return True
                                        print(f"Failed to download video: {str(e)}")
                                        continue
                                    
                                    # Verify the file was actually downloaded
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
                # If we get here but the file exists, it was actually successful
                if os.path.exists(final_trailer_filename):
                    print(f"Trailer exists despite error: {str(e)}")
                    return True
                print(f"Unexpected error downloading trailer for '{movie_title} ({movie_year})': {str(e)}")
                return False

    else:
            # Quiet version with minimal output
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
                                            # Check if the file exists despite the max downloads message
                                            if os.path.exists(final_trailer_filename):
                                                print_colored("Trailer download successful", 'green')
                                                return True
                                        continue
    
                                    # Verify the file was actually downloaded
                                    if os.path.exists(final_trailer_filename):
                                        print_colored("Trailer download successful", 'green')
                                        return True
                    return False
                except Exception as e:
                    # If we get here but the file exists, it was actually successful
                    if os.path.exists(final_trailer_filename):
                        print_colored("Trailer download successful", 'green')
                        return True
                    print_colored("Trailer download failed. Turn on SHOW_YT_DLP_PROGRESS for more info", 'red')
                    return False
    
    # Clean up any partial downloads
    cleanup_trailer_files(sanitized_title, movie_year, trailers_folder)
    return False

# Main processing
start_time = datetime.now()
print_colored(f"\nChecking your {MOVIE_LIBRARY_NAME} library for missing trailers", 'blue')

# Conditionally fetch movies based on USE_LABELS setting
if USE_LABELS:
    # Get movies without MTDfP label using filters
    filters = {
        'and': [
            {'label!': 'MTDfP'}   # Movies without MTDfP label
        ]
    }
    all_movies = plex.library.section(MOVIE_LIBRARY_NAME).search(filters=filters)
    print_colored(f"Found {len(all_movies)} movies without MTDfP label", 'blue')
else:
    # Get all movies (v1 behavior)
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

    if CHECK_PLEX_PASS_TRAILERS:
        # Check Plex extras for a 'trailer' subtype
        trailers = [
            extra
            for extra in movie.extras()
            if extra.type == 'clip' and extra.subtype == 'trailer'
        ]
        already_has_trailer = bool(trailers)
    else:
        # Check only the local filesystem for a trailer
        # But first apply path mapping if needed
        mapped_path = map_path_if_needed(movie.locations[0])
        already_has_trailer = has_local_trailer(mapped_path)

    if not already_has_trailer:
        # No trailer found
        if DOWNLOAD_TRAILERS:
            movie_path = movie.locations[0]
            success = download_trailer(movie.title, movie.year, movie_path)
            if success:
                movies_with_downloaded_trailers[(movie.title, movie.year)] = movie.ratingKey
                if (movie.title, movie.year) in movies_download_errors:
                    movies_download_errors.remove((movie.title, movie.year))
                if (movie.title, movie.year) in movies_missing_trailers:
                    movies_missing_trailers.remove((movie.title, movie.year))
                # Add MTDfP label after successful trailer download (only if USE_LABELS is True)
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
        # Movie already has a trailer, add MTDfP label (only if USE_LABELS is True)
        if USE_LABELS:
            add_mtdfp_label(movie, "already has trailer")

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
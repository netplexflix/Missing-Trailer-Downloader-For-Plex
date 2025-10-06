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
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
TV_GENRES_TO_SKIP = config.get('TV_GENRES_TO_SKIP', [])
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
CHECK_PLEX_PASS_TRAILERS = config.get('CHECK_PLEX_PASS_TRAILERS', True)
MAP_PATH = config.get('MAP_PATH', False)
PATH_MAPPINGS = config.get('PATH_MAPPINGS', {})
USE_LABELS = config.get('USE_LABELS', False)

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)
tv_section = plex.library.section(TV_LIBRARY_NAME)

# Print configuration
print("\nConfiguration for this run:")
print(f"TV_LIBRARY_NAME: {TV_LIBRARY_NAME}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"TV_GENRES_TO_SKIP: {', '.join(TV_GENRES_TO_SKIP)}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"MAP_PATH: {GREEN}true{RESET}" if MAP_PATH else f"MAP_PATH: {ORANGE}false{RESET}")
print(f"USE_LABELS: {GREEN}true{RESET}" if USE_LABELS else f"USE_LABELS: {ORANGE}false{RESET}")

if MAP_PATH:
    print("PATH_MAPPINGS:")
    for src, dst in PATH_MAPPINGS.items():
        print(f"  '{src}' => '{dst}'")

# Lists to store the status of trailer downloads
shows_with_downloaded_trailers = {}
shows_download_errors = []
shows_skipped = []
shows_missing_trailers = []

def map_path_if_needed(original_path):
    """
    If MAP_PATH is True, replace any matching prefix from PATH_MAPPINGS
    with its mapped value. Otherwise, return the path as-is.
    """
    if not MAP_PATH or not PATH_MAPPINGS:
        return original_path

    sorted_mappings = sorted(PATH_MAPPINGS.items(), key=lambda x: len(x[0]), reverse=True)
    for source_prefix, dest_prefix in sorted_mappings:
        if original_path.startswith(source_prefix):
            mapped_path = original_path.replace(source_prefix, dest_prefix, 1)
            print(f"Mapping path: '{original_path}' => '{mapped_path}'")
            return mapped_path

    return original_path

def add_mtdfp_label(show, context=""):
    """
    Add MTDfP label to a TV show if it doesn't already have it.
    Only called when USE_LABELS is True.
    
    Args:
        show: The TV show object to add the label to
        context: Optional context string for logging (e.g., "already has trailer")
    """
    try:
        # First unlock the labels field
        show.edit(**{'label.locked': 0})
        
        # Check if MTDfP label already exists
        existing_labels = [label.tag for label in (show.labels or [])]
        if 'MTDfP' not in existing_labels:
            # Use addLabel method which works
            show.addLabel('MTDfP')
            context_text = f" ({context})" if context else ""
            print_colored(f"Added MTDfP label to '{show.title}'{context_text}", 'green')
        else:
            print_colored(f"TV show '{show.title}' already has MTDfP label", 'blue')
    except Exception as e:
        print_colored(f"Failed to add MTDfP label to '{show.title}': {e}", 'red')

def short_videos_only(info_dict, incomplete=False):
    """
    A match-filter function for yt-dlp that rejects videos over 5 minutes (300 seconds).
    Return None if acceptable; return a string (reason) if the video should be skipped.
    """
    duration = info_dict.get('duration')
    
    # Add debug logging
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

def cleanup_trailer_files(show_title, trailers_folder):
    """
    Remove any leftover partial download files that match our trailer filename prefix
    (excluding the final .mp4).
    """
    for file in os.listdir(trailers_folder):
        if file.startswith(f"{show_title}-trailer.") and not file.endswith(".mp4"):
            try:
                os.remove(os.path.join(trailers_folder, file))
            except OSError as e:
                print(f"Failed to delete {file}: {e}")

def has_local_trailer(show_directory):
    """
    Check for an existing local trailer:
      1) File named '*-trailer' in the show_directory.
      2) A subfolder named 'Trailers' with at least one video file.
    """
    # Apply path mapping first
    mapped_directory = map_path_if_needed(show_directory)

    # If folder doesn't exist or is inaccessible, return False
    if not os.path.isdir(mapped_directory):
        print(f"Warning: Cannot access directory: {mapped_directory}")
        return False

    try:
        contents = os.listdir(mapped_directory)
    except OSError as e:
        print(f"Warning: Error listing directory '{mapped_directory}': {e}")
        return False

    # 1) Look for any '*-trailer' file
    for f in contents:
        if f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            name_without_ext, _ = os.path.splitext(f.lower())
            if name_without_ext.endswith("-trailer"):
                return True

    # 2) Check for a 'Trailers' subfolder with at least one video file
    trailers_subfolder = os.path.join(mapped_directory, "Trailers")
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
    """
    Attempt to download a trailer for the TV show using YouTube search.
    """
    # Sanitize show_title to remove or replace problematic characters
    sanitized_title = show_title.replace(":", " -")

    # Extract key terms from show title (for title matching)
    key_terms = show_title.lower().split(":")
    main_title = key_terms[0].strip()
    subtitle = key_terms[1].strip() if len(key_terms) > 1 else None

    # Prepare the search query
    search_query = f"ytsearch10:{show_title} TV show official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"

    # Map the path for local usage
    mapped_directory = map_path_if_needed(show_directory)
    trailers_directory = os.path.join(mapped_directory, 'Trailers')

    # Create or reuse the folder
    os.makedirs(trailers_directory, exist_ok=True)

    output_filename = os.path.join(
        trailers_directory,
        f"{sanitized_title}-trailer.%(ext)s"
    )
    final_trailer_filename = os.path.join(
        trailers_directory,
        f"{sanitized_title}-trailer.mp4"
    )

    # If there's already a trailer file, skip download
    if os.path.exists(final_trailer_filename):
        return True

    def verify_title_match(video_title, show_title):
        """
        Verify that the video title is a valid match for the show.
        Improved to handle show titles with years in parentheses.
        """
        video_title = video_title.lower()
        
        # Handle shows with years in parentheses - extract base title and year
        import re
        year_match = re.search(r'\((\d{4})\)', show_title)
        year = year_match.group(1) if year_match else None
        base_title = re.sub(r'\s*\(\d{4}\)\s*', '', show_title).lower().strip()
        
        # Check for different scenarios of matching
        
        # 1. If the base title (without year) is in the video title
        if base_title in video_title:
            # If there's a year, check if it's also in the video title
            if year and year in video_title:
                return True
            # If no year in show title or we're being lenient about the year
            elif not year:
                return True
        
        # 2. Original split-based check (for backwards compatibility)
        show_title_parts = show_title.lower().split(':')
        if all(part.strip() in video_title for part in show_title_parts):
            return True
            
        # 3. Exact title match
        if show_title.lower() in video_title:
            return True
        
        # 4. Special case for titles with years
        if year and base_title in video_title and year in video_title:
            return True
            
        return False

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
                                if verify_title_match(video_title, show_title):
                                    try:
                                        ydl.download([video['url']])
                                    except yt_dlp.utils.DownloadError as e:
                                        if "has already been downloaded" in str(e):
                                            print("Trailer already exists")
                                            return True
                                        if "Maximum number of downloads reached" in str(e):
                                            # Check if the file exists despite the max downloads message
                                            if os.path.exists(final_trailer_filename):
                                                print(f"Trailer successfully downloaded for '{show_title}'")
                                                return True
                                        print(f"Failed to download video: {str(e)}")
                                        continue
                                    
                                    # Verify the file was actually downloaded
                                    if os.path.exists(final_trailer_filename):
                                        print(f"Trailer successfully downloaded for '{show_title}'")
                                        return True
                                else:
                                    print(f"Skipping video - title doesn't match show title")
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
                print(f"Unexpected error downloading trailer for '{show_title}': {str(e)}")
                return False

    else:
        # Quiet version with minimal output
        print(f"Searching trailer for {show_title}...")
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
                            if duration <= 300 and verify_title_match(video.get('title', ''), show_title):
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
    cleanup_trailer_files(sanitized_title, trailers_directory)
    return False

# Main processing
start_time = datetime.now()
print_colored(f"\nChecking your {TV_LIBRARY_NAME} library for missing trailers", 'blue')

# Conditionally fetch TV shows based on USE_LABELS setting
if USE_LABELS:
    # Get TV shows without MTDfP label using filters
    filters = {
        'and': [
            {'label!': 'MTDfP'}   # TV shows without MTDfP label
        ]
    }
    all_shows = tv_section.search(filters=filters)
    print_colored(f"Found {len(all_shows)} TV shows without MTDfP label", 'blue')
else:
    # Get all TV shows (v1 behavior)
    all_shows = tv_section.all()

total_shows = len(all_shows)

for index, show in enumerate(all_shows, start=1):
    print(f"Checking show {index}/{total_shows}: {show.title}")
    show.reload()

    # Skip if show has any genres in the skip list
    show_genres = [genre.tag.lower() for genre in (show.genres or [])]
    if any(skip_genre.lower() in show_genres for skip_genre in TV_GENRES_TO_SKIP):
        print(f"Skipping '{show.title}' (Genres match skip list: {', '.join(show_genres)})")
        shows_skipped.append(show.title)
        continue

    # If CHECK_PLEX_PASS_TRAILERS is True => check Plex extras
    # If False => check only local trailer files (using mapped path)
    if CHECK_PLEX_PASS_TRAILERS:
        trailers = [
            extra for extra in show.extras()
            if extra.type == 'clip' and extra.subtype == 'trailer'
        ]
        already_has_trailer = bool(trailers)
    else:
        already_has_trailer = has_local_trailer(show.locations[0])

    if not already_has_trailer:
        # No trailer found
        if DOWNLOAD_TRAILERS:
            show_directory = show.locations[0]
            success = download_trailer(show.title, show_directory)
            if success:
                folder_name = os.path.basename(map_path_if_needed(show_directory))
                shows_with_downloaded_trailers[folder_name] = show.ratingKey
                if show.title in shows_download_errors:
                    shows_download_errors.remove(show.title)
                if show.title in shows_missing_trailers:
                    shows_missing_trailers.remove(show.title)
                # Add MTDfP label after successful trailer download (only if USE_LABELS is True)
                if USE_LABELS:
                    add_mtdfp_label(show)
            else:
                if show.title not in shows_download_errors:
                    shows_download_errors.append(show.title)
                if show.title not in shows_missing_trailers:
                    shows_missing_trailers.append(show.title)
        else:
            shows_missing_trailers.append(show.title)
    else:
        # Show already has a trailer, add MTDfP label (only if USE_LABELS is True)
        if USE_LABELS:
            add_mtdfp_label(show, "already has trailer")

# Summaries
if shows_skipped:
    print("\n")
    print_colored("TV Shows skipped (Matching Genre):", 'yellow')
    for show in sorted(shows_skipped):
        print(show)

if shows_missing_trailers:
    print("\n")
    print_colored("TV Shows missing trailers:", 'red')
    # Exclude any that might also be in the skipped list
    for show in sorted(set(shows_missing_trailers) - set(shows_skipped)):
        print(show)

if shows_with_downloaded_trailers:
    print("\n")
    print_colored("TV Shows with successfully downloaded trailers:", 'green')
    for show_folder in sorted(shows_with_downloaded_trailers.keys()):
        print(show_folder)

# Refresh metadata for any newly downloaded trailers
if REFRESH_METADATA and shows_with_downloaded_trailers:
    print_colored("\nRefreshing metadata for TV shows with new trailers:", 'blue')
    for folder_name, rating_key in shows_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"Failed to refresh metadata for '{folder_name}': {e}")

if shows_download_errors:
    print("\n")
    print_colored("TV Shows with failed trailer downloads:", 'red')
    for show in sorted(set(shows_download_errors)):
        print(show)

# If none are missing, none failed, and none downloaded, everything is good!
if not shows_missing_trailers and not shows_download_errors and not shows_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)
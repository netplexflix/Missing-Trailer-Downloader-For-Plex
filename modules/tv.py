import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime

# Set up logging
logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "tv")
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

def parse_library_names(library_string):
    """
    Parse comma-separated library names and return a list of library names.
    Strips whitespace from each name.
    """
    if not library_string:
        return []
    return [name.strip() for name in library_string.split(',') if name.strip()]

# --- Configuration ---
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.yml')
with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')
TV_LIBRARY_NAMES = parse_library_names(config.get('TV_LIBRARY_NAME', ''))
REFRESH_METADATA = config.get('REFRESH_METADATA')
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
TV_GENRES_TO_SKIP = config.get('TV_GENRES_TO_SKIP', [])
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
CHECK_PLEX_PASS_TRAILERS = config.get('CHECK_PLEX_PASS_TRAILERS', True)

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Print configuration
print("\nConfiguration for this run:")
print(f"TV_LIBRARY_NAMES: {', '.join(TV_LIBRARY_NAMES)}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"TV_GENRES_TO_SKIP: {', '.join(TV_GENRES_TO_SKIP)}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")

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
    mapped_directory = show_directory

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

def verify_title_match(video_title, show_title):
    """
    Verify that the video title is a valid match for the show.
    Improved to handle show titles with years in parentheses and ampersand variations.
    """
    video_title = video_title.lower()
    
    # Handle shows with years in parentheses - extract base title and year
    import re
    year_match = re.search(r'\((\d{4})\)', show_title)
    year = year_match.group(1) if year_match else None
    base_title = re.sub(r'\s*\(\d{4}\)\s*', '', show_title).strip()
    
    # Normalize both titles for better matching
    def normalize_title(title):
        """Normalize title for matching by handling common variations"""
        title = title.lower()
        # Handle ampersand variations
        title = title.replace(' & ', ' and ')
        title = title.replace('&', 'and')
        # Remove extra whitespace
        title = ' '.join(title.split())
        # Remove common punctuation that might cause issues
        title = re.sub(r'[^\w\s]', ' ', title)
        title = ' '.join(title.split())  # Clean up extra spaces again
        return title
    
    normalized_video_title = normalize_title(video_title)
    normalized_base_title = normalize_title(base_title)
    normalized_full_title = normalize_title(show_title)
    
    print(f"Comparing normalized titles:")
    print(f"  Video: '{normalized_video_title}'")
    print(f"  Show (base): '{normalized_base_title}'")
    print(f"  Show (full): '{normalized_full_title}'")
    
    # Check for different scenarios of matching
    
    # 1. If the normalized base title is in the video title
    if normalized_base_title in normalized_video_title:
        print(f"Match found: Base title '{normalized_base_title}' found in video title")
        # If there's a year, check if it's also in the video title (optional)
        if year and year in video_title:
            print(f"Year {year} also found in video title")
            return True
        # If no year in show title or we're being lenient about the year
        elif not year:
            print(f"No year specified, accepting match")
            return True
        else:
            print(f"Year {year} not found in video title, but accepting match anyway")
            return True
    
    # 2. Check if key words from the title are present (for more flexible matching)
    base_words = set(normalized_base_title.split())
    video_words = set(normalized_video_title.split())
    
    # Remove common words that don't help with matching
    common_words = {'the', 'and', 'of', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'with', 'by'}
    base_words = base_words - common_words
    video_words = video_words - common_words
    
    if base_words and len(base_words) > 0:
        # Check if a significant portion of the base title words are in the video title
        matching_words = base_words.intersection(video_words)
        match_ratio = len(matching_words) / len(base_words)
        
        print(f"Word matching - Base words: {base_words}")
        print(f"Word matching - Video words: {video_words}")
        print(f"Word matching - Matching words: {matching_words}")
        print(f"Word matching - Match ratio: {match_ratio}")
        
        if match_ratio >= 0.6:  # At least 60% of words match
            print(f"Word-based match found with {match_ratio:.1%} ratio")
            return True
    
    # 3. Original split-based check (for backwards compatibility with colon-separated titles)
    if ':' in show_title:
        show_title_parts = [normalize_title(part.strip()) for part in show_title.split(':')]
        if all(part in normalized_video_title for part in show_title_parts if part):
            print(f"Colon-separated parts match found")
            return True
    
    # 4. Exact normalized title match
    if normalized_full_title in normalized_video_title:
        print(f"Full normalized title match found")
        return True
    
    print(f"No match found between video and show title")
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

    # Prepare the search query - make it more flexible for shows with special characters
    base_search_title = show_title.replace(" & ", " and ").replace("&", " and ")
    search_query = f"ytsearch15:{base_search_title} TV show official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"

    # Map the path for local usage
    trailers_directory = os.path.join(show_directory, 'Trailers')

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

    # Get skip channels configuration
    skip_channels = config.get('SKIP_CHANNELS', [])
    if isinstance(skip_channels, str):
        skip_channels = [ch.strip() for ch in skip_channels.split(',') if ch.strip()]

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
        'default_search': 'ytsearch15',
        'extract_flat': 'in_playlist',
        'force_generic_extractor': False,
        'ignoreerrors': True,
        'quiet': not SHOW_YT_DLP_PROGRESS,
        'no_warnings': not SHOW_YT_DLP_PROGRESS
    }

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
            if skip_channels:
                print(f"Skip channels: {skip_channels}")
            try:
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    entries = list(filter(None, info['entries']))

                    for video in entries:
                        if video:
                            duration = video.get('duration', 0)
                            video_title = video.get('title', '')
                            uploader = video.get('uploader', 'Unknown')
                            print(f"Found video: {video_title} (Duration: {duration} seconds) by {uploader}")

                            # Check if uploader is in skip list
                            if skip_channels and any(ch.lower() in uploader.lower() for ch in skip_channels):
                                print(f"Skipping video from channel: {uploader} (in skip list)")
                                continue

                            if duration and duration <= 300:
                                if verify_title_match(video_title, show_title):
                                    try:
                                        print(f"Attempting to download: {video_title}")
                                        ydl.download([video['url']])
                                    except yt_dlp.utils.DownloadError as e:
                                        if "has already been downloaded" in str(e):
                                            print("Trailer already exists")
                                            return True
                                        if "Maximum number of downloads reached" in str(e):
                                            if os.path.exists(final_trailer_filename):
                                                print(f"Trailer successfully downloaded for '{show_title}'")
                                                return True
                                        print(f"Failed to download video: {str(e)}")
                                        continue

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
                if os.path.exists(final_trailer_filename):
                    print(f"Trailer exists despite error: {str(e)}")
                    return True
                print(f"Unexpected error downloading trailer for '{show_title}': {str(e)}")
                return False

    else:
        # Quiet version
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
                            uploader = video.get('uploader', 'Unknown')

                            if skip_channels and any(ch.lower() in uploader.lower() for ch in skip_channels):
                                continue

                            if duration <= 300 and verify_title_match(video.get('title', ''), show_title):
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

    cleanup_trailer_files(sanitized_title, trailers_directory)
    return False

def process_library(library_name, shows_with_downloaded_trailers, shows_download_errors, 
                   shows_skipped, shows_missing_trailers):
    """Process a single TV library for trailers."""
    try:
        print_colored(f"\nChecking library '{library_name}' for missing trailers", 'blue')
        tv_section = plex.library.section(library_name)
        all_shows = tv_section.all()
        total_shows = len(all_shows)
        
        for index, show in enumerate(all_shows, start=1):
            print(f"[{library_name}] Checking show {index}/{total_shows}: {show.title}")
            show.reload()

            # Skip if show has any genres in the skip list
            show_genres = [genre.tag.lower() for genre in (show.genres or [])]
            if any(skip_genre.lower() in show_genres for skip_genre in TV_GENRES_TO_SKIP):
                print(f"Skipping '{show.title}' (Genres match skip list: {', '.join(show_genres)})")
                shows_skipped.append((show.title, library_name))
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
                        folder_name = os.path.basename(show_directory)
                        shows_with_downloaded_trailers[(show.title, library_name)] = show.ratingKey
                        # Remove from error lists if it was there
                        error_key = (show.title, library_name)
                        if error_key in shows_download_errors:
                            shows_download_errors.remove(error_key)
                        if error_key in shows_missing_trailers:
                            shows_missing_trailers.remove(error_key)
                    else:
                        error_key = (show.title, library_name)
                        if error_key not in shows_download_errors:
                            shows_download_errors.append(error_key)
                        if error_key not in shows_missing_trailers:
                            shows_missing_trailers.append(error_key)
                else:
                    shows_missing_trailers.append((show.title, library_name))
                    
    except Exception as e:
        print_colored(f"Error processing library '{library_name}': {str(e)}", 'red')
        print(f"Skipping library '{library_name}'")

# Main processing
start_time = datetime.now()

# Initialize lists to store the status of trailer downloads
shows_with_downloaded_trailers = {}
shows_download_errors = []
shows_skipped = []
shows_missing_trailers = []

if not TV_LIBRARY_NAMES:
    print_colored("No TV libraries configured. Please set TV_LIBRARY_NAME in config.yml", 'red')
    sys.exit(1)

# Process each library
for library_name in TV_LIBRARY_NAMES:
    try:
        # Check if library exists
        plex.library.section(library_name)
        process_library(library_name, shows_with_downloaded_trailers, shows_download_errors, 
                       shows_skipped, shows_missing_trailers)
    except Exception as e:
        print_colored(f"Library '{library_name}' not found or accessible: {str(e)}", 'red')
        continue

# Summaries
if shows_skipped:
    print("\n")
    print_colored("TV Shows skipped (Matching Genre):", 'yellow')
    for show, library in sorted(shows_skipped):
        print(f"[{library}] {show}")

if shows_missing_trailers:
    print("\n")
    print_colored("TV Shows missing trailers:", 'red')
    for show, library in sorted(shows_missing_trailers):
        print(f"[{library}] {show}")

if shows_with_downloaded_trailers:
    print("\n")
    print_colored("TV Shows with successfully downloaded trailers:", 'green')
    for (show, library), rating_key in sorted(shows_with_downloaded_trailers.items()):
        print(f"[{library}] {show}")

# Refresh metadata for any newly downloaded trailers
if REFRESH_METADATA and shows_with_downloaded_trailers:
    print_colored("\nRefreshing metadata for TV shows with new trailers:", 'blue')
    for (show, library), rating_key in shows_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"[{library}] Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"[{library}] Failed to refresh metadata for '{show}': {e}")

if shows_download_errors:
    print("\n")
    print_colored("TV Shows with failed trailer downloads:", 'red')
    for show, library in sorted(shows_download_errors):
        print(f"[{library}] {show}")

# If none are missing, none failed, and none downloaded, everything is good!
if not shows_missing_trailers and not shows_download_errors and not shows_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)
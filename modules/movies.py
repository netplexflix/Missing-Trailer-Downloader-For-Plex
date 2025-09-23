import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime

# Set up logging
logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "movies")
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

# Load configuration from config.yml
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.yml')
with open(config_path, 'r') as config_file:
    config = yaml.safe_load(config_file)

# Configuration variables
PLEX_URL = config.get('PLEX_URL')
PLEX_TOKEN = config.get('PLEX_TOKEN')
MOVIE_LIBRARY_NAMES = parse_library_names(config.get('MOVIE_LIBRARY_NAME', ''))
MOVIE_GENRES_TO_SKIP = config.get('MOVIE_GENRES_TO_SKIP', [])
DOWNLOAD_TRAILERS = config.get('DOWNLOAD_TRAILERS')
PREFERRED_LANGUAGE = config.get('PREFERRED_LANGUAGE', 'original')
REFRESH_METADATA = config.get('REFRESH_METADATA')
SHOW_YT_DLP_PROGRESS = config.get('SHOW_YT_DLP_PROGRESS', True)
CHECK_PLEX_PASS_TRAILERS = config.get('CHECK_PLEX_PASS_TRAILERS', True)

# Print configuration settings
print("\nConfiguration for this run:")
print(f"MOVIE_LIBRARY_NAMES: {', '.join(MOVIE_LIBRARY_NAMES)}")
print(f"CHECK_PLEX_PASS_TRAILERS: {GREEN}true{RESET}" if CHECK_PLEX_PASS_TRAILERS else f"CHECK_PLEX_PASS_TRAILERS: {ORANGE}false{RESET}")
print(f"MOVIE_GENRES_TO_SKIP: {', '.join(MOVIE_GENRES_TO_SKIP)}")
print(f"DOWNLOAD_TRAILERS: {GREEN}true{RESET}" if DOWNLOAD_TRAILERS else f"DOWNLOAD_TRAILERS: {ORANGE}false{RESET}")
print(f"PREFERRED_LANGUAGE: {PREFERRED_LANGUAGE}")
print(f"SHOW_YT_DLP_PROGRESS: {GREEN}true{RESET}" if SHOW_YT_DLP_PROGRESS else f"SHOW_YT_DLP_PROGRESS: {ORANGE}false{RESET}")
print(f"REFRESH_METADATA: {GREEN}true{RESET}" if REFRESH_METADATA else f"REFRESH_METADATA: {ORANGE}false{RESET}")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

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

def verify_title_match(video_title, movie_title, year):
    """
    Verify that the video title is a valid match for the movie.
    Improved to handle movie titles with years, special characters, and ampersand variations.
    """
    video_title = video_title.lower()
    movie_title_lower = movie_title.lower()
    year_str = str(year)
    
    # Handle ampersand variations in movie titles
    def normalize_title(title):
        """Normalize title for matching by handling common variations"""
        import re
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
    normalized_movie_title = normalize_title(movie_title_lower)
    
    print(f"Comparing normalized titles:")
    print(f"  Video: '{normalized_video_title}'")
    print(f"  Movie: '{normalized_movie_title}'")
    print(f"  Year: {year_str}")
    
    # 1. Check if year and normalized full title match
    if year_str in video_title and normalized_movie_title in normalized_video_title:
        print(f"Match found: Normalized title '{normalized_movie_title}' and year {year_str} found in video")
        return True
    
    # 2. Check if all parts of a title with colons are present
    movie_title_parts = [normalize_title(part.strip()) for part in movie_title_lower.split(':')]
    if len(movie_title_parts) > 1:
        if all(part in normalized_video_title for part in movie_title_parts if part) and year_str in video_title:
            print(f"Colon-separated parts match with year found")
            return True
    
    # 3. Word-based matching for flexible comparison
    movie_words = set(normalized_movie_title.split())
    video_words = set(normalized_video_title.split())
    
    # Remove common words that don't help with matching
    common_words = {'the', 'and', 'of', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'with', 'by'}
    movie_words = movie_words - common_words
    video_words = video_words - common_words
    
    if movie_words and len(movie_words) > 0 and year_str in video_title:
        # Check if a significant portion of the movie title words are in the video title
        matching_words = movie_words.intersection(video_words)
        match_ratio = len(matching_words) / len(movie_words)
        
        print(f"Word matching - Movie words: {movie_words}")
        print(f"Word matching - Video words: {video_words}")
        print(f"Word matching - Matching words: {matching_words}")
        print(f"Word matching - Match ratio: {match_ratio}")
        
        if match_ratio >= 0.6:  # At least 60% of words match
            print(f"Word-based match found with {match_ratio:.1%} ratio and year {year_str}")
            return True
    
    # 4. For longer titles, check if a substantial portion matches
    if len(movie_title_lower) > 20:
        # Get first 70% of the title
        partial_title = normalized_movie_title[:int(len(normalized_movie_title) * 0.7)]
        if partial_title in normalized_video_title and year_str in video_title:
            print(f"Partial title match found with year")
            return True
    
    # 5. Original fallback check (without year requirement for backwards compatibility)
    if normalized_movie_title in normalized_video_title:
        print(f"Basic normalized title match found (no year requirement)")
        return True
    
    print(f"No match found between video and movie title")
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

    # Prepare the search query with year for better accuracy - handle ampersand variations
    base_search_title = movie_title.replace(" & ", " and ").replace("&", " and ")
    search_query = f"ytsearch15:{base_search_title} {movie_year} official trailer"
    if PREFERRED_LANGUAGE.lower() != "original":
        search_query += f" {PREFERRED_LANGUAGE}"

    movie_folder = os.path.dirname(movie_path)
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
        'default_search': 'ytsearch15',  # Increased from 10 to 15 for better results
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
            if skip_channels:
                print(f"Skip channels: {skip_channels}")
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
                            uploader = video.get('uploader', 'Unknown')
                            print(f"Found video: {video_title} (Duration: {duration} seconds) by {uploader}")
                            
                            # Check if uploader is in skip list
                            if skip_channels and any(ch.lower() in uploader.lower() for ch in skip_channels):
                                print(f"Skipping video from channel: {uploader} (in skip list)")
                                continue
                            
                            # Check both duration and title match
                            if duration and duration <= 300:
                                if verify_title_match(video_title, movie_title, movie_year):
                                    try:
                                        print(f"Attempting to download: {video_title}")
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
                            uploader = video.get('uploader', 'Unknown')
                            
                            # Check if uploader is in skip list
                            if skip_channels and any(ch.lower() in uploader.lower() for ch in skip_channels):
                                continue
                                
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

def process_library(library_name, movies_with_downloaded_trailers, movies_download_errors, 
                   movies_skipped, movies_missing_trailers):
    """Process a single movie library for trailers."""
    try:
        print_colored(f"\nChecking library '{library_name}' for missing trailers", 'blue')
        library_section = plex.library.section(library_name)
        all_movies = library_section.all()
        total_movies = len(all_movies)
        
        for index, movie in enumerate(all_movies, start=1):
            print(f"[{library_name}] Checking movie {index}/{total_movies}: {movie.title}")
            movie.reload()

            # If it has any skip-genres, skip it
            movie_genres = [genre.tag.lower() for genre in (movie.genres or [])]
            if any(skip_genre.lower() in movie_genres for skip_genre in MOVIE_GENRES_TO_SKIP):
                print(f"Skipping '{movie.title}' (Genres match skip list: {', '.join(movie_genres)})")
                movies_skipped.append((movie.title, movie.year, library_name))
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
                already_has_trailer = has_local_trailer(movie.locations[0])

            if not already_has_trailer:
                # No trailer found
                if DOWNLOAD_TRAILERS:
                    movie_path = movie.locations[0]
                    success = download_trailer(movie.title, movie.year, movie_path)
                    if success:
                        movies_with_downloaded_trailers[(movie.title, movie.year, library_name)] = movie.ratingKey
                        # Remove from error lists if it was there
                        error_key = (movie.title, movie.year, library_name)
                        if error_key in movies_download_errors:
                            movies_download_errors.remove(error_key)
                        if error_key in movies_missing_trailers:
                            movies_missing_trailers.remove(error_key)
                    else:
                        error_key = (movie.title, movie.year, library_name)
                        if error_key not in movies_download_errors:
                            movies_download_errors.append(error_key)
                        if error_key not in movies_missing_trailers:
                            movies_missing_trailers.append(error_key)
                else:
                    movies_missing_trailers.append((movie.title, movie.year, library_name))
                    
    except Exception as e:
        print_colored(f"Error processing library '{library_name}': {str(e)}", 'red')
        print(f"Skipping library '{library_name}'")

# Main processing
start_time = datetime.now()

# Initialize lists to store movie trailer status
movies_with_downloaded_trailers = {}
movies_download_errors = []
movies_skipped = []
movies_missing_trailers = []

if not MOVIE_LIBRARY_NAMES:
    print_colored("No movie libraries configured. Please set MOVIE_LIBRARY_NAME in config.yml", 'red')
    sys.exit(1)

# Process each library
for library_name in MOVIE_LIBRARY_NAMES:
    try:
        # Check if library exists
        plex.library.section(library_name)
        process_library(library_name, movies_with_downloaded_trailers, movies_download_errors, 
                       movies_skipped, movies_missing_trailers)
    except Exception as e:
        print_colored(f"Library '{library_name}' not found or accessible: {str(e)}", 'red')
        continue

# Print the results
if movies_skipped:
    print("\n")
    print_colored("Movies skipped (Matching Genre):", 'yellow')
    for title, year, library in sorted(movies_skipped):
        print(f"[{library}] {title} ({year})")

if movies_missing_trailers:
    print("\n")
    print_colored("Movies missing trailers:", 'red')
    for title, year, library in sorted(movies_missing_trailers):
        print(f"[{library}] {title} ({year})")

if movies_with_downloaded_trailers:
    print("\n")
    print_colored("Movies with successfully downloaded trailers:", 'green')
    for (title, year, library), rating_key in sorted(movies_with_downloaded_trailers.items()):
        print(f"[{library}] {title} ({year})")

if REFRESH_METADATA and movies_with_downloaded_trailers:
    print_colored("\nRefreshing metadata for movies with new trailers:", 'blue')
    for (title, year, library), rating_key in movies_with_downloaded_trailers.items():
        if rating_key:
            try:
                item = plex.fetchItem(rating_key)
                print(f"[{library}] Refreshing metadata for '{item.title}'")
                item.refresh()
            except Exception as e:
                print(f"[{library}] Failed to refresh metadata for '{title} ({year})': {e}")

if movies_download_errors:
    print("\n")
    print_colored("Movies with failed trailer downloads:", 'red')
    for title, year, library in sorted(movies_download_errors):
        print(f"[{library}] {title} ({year})")

if not movies_missing_trailers and not movies_download_errors and not movies_with_downloaded_trailers:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)
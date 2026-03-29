import os
import sys
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime
import shlex
from pathlib import Path


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
    # Fallback to old single library format for backward compatibility
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
TRAILER_RESOLUTION_MAX = int(config.get('TRAILER_RESOLUTION_MAX', 1080))
TRAILER_RESOLUTION_MIN = int(config.get('TRAILER_RESOLUTION_MIN', 1080))
if TRAILER_RESOLUTION_MIN > TRAILER_RESOLUTION_MAX:
    TRAILER_RESOLUTION_MIN, TRAILER_RESOLUTION_MAX = TRAILER_RESOLUTION_MAX, TRAILER_RESOLUTION_MIN
TRAILER_FILE_FORMAT = config.get('TRAILER_FILE_FORMAT', 'mkv').lower()
if TRAILER_FILE_FORMAT not in ('mkv', 'mp4'):
    TRAILER_FILE_FORMAT = 'mkv'

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
if YT_DLP_CUSTOM_OPTIONS:
    print(f"YT_DLP_CUSTOM_OPTIONS: {', '.join(YT_DLP_CUSTOM_OPTIONS)}")
if IS_DOCKER:
    print(f"Running in: {GREEN}Docker Container{RESET}")

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Initialize trailer tracker for dashboard carousel
try:
    from Modules.trailer_tracker import TrailerTracker
except ImportError:
    from trailer_tracker import TrailerTracker
_trailer_tracker = TrailerTracker()

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

NEGATIVE_TITLE_KEYWORDS = [
    'reaction', 'react', 'review', 'behind the scenes',
    'making of', 'breakdown', 'explained', 'analysis', 'fan made',
    'fan-made', 'parody', 'spoof', 'honest trailer', 'honest trailers',
    'everything wrong', 'pitch meeting', 'recap', 'summary',
    'cast interview', 'press tour', 'red carpet', 'premiere',
    'deleted scene', 'bloopers', 'gag reel', 'easter egg',
    'theory', 'theories', 'predictions', 'ending explained',
    'watch along', 'commentary', 'video essay', 'ranking',
    'top 10', 'every trailer', 'all trailers', 'trailer compilation',
]

PREFERRED_CHANNEL_KEYWORDS = [
    'official', 'vevo', 'pictures', 'studios', 'entertainment',
    'warner', 'universal', 'sony', 'disney', 'paramount', 'lionsgate',
    'a24', 'fox', 'mgm', 'hbo', 'netflix', 'hulu', 'amazon', 'apple tv',
    'peacock', 'showtime', 'starz', 'amc', 'fx', 'bbc', 'cbs', 'nbc', 'abc',
]

TRAILER_NOISE_WORDS = {
    'official', 'new', 'exclusive', 'international', 'final', 'first',
    'full', 'main', 'original', 'extended', 'teaser', 'trailer',
    'hd', '4k', 'uhd', 'imax', 'dolby', 'restoration',
    'tv', 'spot', 'clip', 'promo', 'preview', 'sneak', 'peek',
}

def is_likely_trailer(video_title):
    """Returns False if video title contains non-trailer keywords."""
    title_lower = video_title.lower()
    return not any(kw in title_lower for kw in NEGATIVE_TITLE_KEYWORDS)

def is_standalone_title_match(movie_title_lower, video_title_lower):
    """Check if movie title appears as standalone phrase, not part of a longer movie name."""
    import re
    pattern = r'\b' + re.escape(movie_title_lower) + r'\b'
    match = re.search(pattern, video_title_lower)
    if not match:
        return False

    # For short titles (1-2 words), reject if significant words precede the title
    # (e.g., "Burden of" before "Dreams" means it's a different movie)
    movie_words = movie_title_lower.split()
    if len(movie_words) <= 2:
        prefix = video_title_lower[:match.start()].strip()
        if prefix:
            prefix = re.sub(r'[|\-:!]', ' ', prefix).strip()
            prefix_words = prefix.split()
            significant = [w for w in prefix_words if w not in TRAILER_NOISE_WORDS and len(w) > 2]
            if significant:
                return False
    return True

def score_video(video):
    """Score video by likelihood of being an official trailer. Higher = better."""
    score = 0
    channel = (video.get('channel', '') or video.get('uploader', '') or '').lower()
    title = (video.get('title', '') or '').lower()

    if 'official' in title:
        score += 2
    if 'trailer' in title:
        score += 2
    for kw in PREFERRED_CHANNEL_KEYWORDS:
        if kw in channel:
            score += 3
            break
    view_count = video.get('view_count', 0) or 0
    if view_count > 1_000_000:
        score += 2
    elif view_count > 100_000:
        score += 1

    # Search position bonus (YouTube relevance signal)
    position = video.get('_search_position', 99)
    if position == 0:
        score += 3
    elif position == 1:
        score += 2
    elif position <= 3:
        score += 1

    # Year-mismatch penalty: if the video title names a different year, deprioritize
    import re
    movie_year = str(video.get('_movie_year', ''))
    if movie_year:
        years_in_title = re.findall(r'\b((?:19|20)\d{2})\b', title)
        if years_in_title and movie_year not in years_in_title:
            score -= 3

    return score

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

def normalize_path_for_docker(path):
    """
    Normalize paths for Docker compatibility.
    - Unix paths (starting with /) are returned as-is
    - Windows paths keep drive letter as first directory to avoid collisions
    """
    if not IS_DOCKER:
        return path
    
    # If it's already a Unix-style path, return as-is
    if path.startswith('/'):
        return path
    
    # Handle Windows paths: preserve drive letter to avoid collisions
    import re
    drive_match = re.match(r'^([A-Za-z]):', path)
    
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        # Remove drive letter and colon
        path_without_drive = path[2:]
        # Convert backslashes to forward slashes
        path_normalized = path_without_drive.replace('\\', '/')
        # Prepend drive as first directory
        result = f'/{drive_letter}{path_normalized}'
        print(f"Path normalized: {path} -> {result}")
        return result
    
    # Fallback: just convert backslashes
    return path.replace('\\', '/')

def cleanup_trailer_files(movie_title, movie_year, trailers_folder):
    """
    Remove any leftover partial download files that match our trailer filename prefix
    (excluding the final .mp4).
    """
    for file in os.listdir(trailers_folder):
        if file.startswith(f"{movie_title} ({movie_year})-trailer.") and not file.endswith(f".{TRAILER_FILE_FORMAT}"):
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

def download_trailer(movie_title, movie_year, movie_path, trailer_tracker=None, plex_rating_key=None):
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

    # Prepare search queries — primary and fallback with different phrasing
    # yt-dlp's search API can return different results than browser YouTube,
    # so we try multiple query variations to maximize chances of finding the correct trailer
    language_suffix = f" {PREFERRED_LANGUAGE}" if PREFERRED_LANGUAGE.lower() != "original" else ""
    search_queries = [
        f"ytsearch15:{movie_title} {movie_year} official trailer{language_suffix}",
        f"ytsearch15:{movie_title} trailer {movie_year}{language_suffix}",
        f"ytsearch15:{movie_title} {movie_year} movie trailer{language_suffix}",
    ]
    search_query = search_queries[0]

    # Build the 'Trailers' subfolder
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
        f"{sanitized_title} ({movie_year})-trailer.{TRAILER_FILE_FORMAT}"
    )

    trailer_base_name = f"{sanitized_title} ({movie_year})-trailer"
    VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')

    def _find_downloaded_trailer():
        """Check if any trailer file exists (any video extension) and return its path."""
        if os.path.exists(final_trailer_filename):
            return final_trailer_filename
        try:
            for f in os.listdir(trailers_folder):
                name, ext = os.path.splitext(f)
                if name == trailer_base_name and ext.lower() in VIDEO_EXTENSIONS:
                    return os.path.join(trailers_folder, f)
        except OSError:
            pass
        return None

    def _track_downloaded_trailer():
        """Record the downloaded trailer in the tracker for the dashboard carousel."""
        trailer_path = _find_downloaded_trailer()
        if trailer_tracker and trailer_path:
            trailer_tracker.add_trailer(
                file_path=trailer_path,
                title=movie_title,
                year=str(movie_year),
                media_type="movie",
                plex_rating_key=str(plex_rating_key) if plex_rating_key else "",
                poster_url=f"/api/plex/poster/{plex_rating_key}" if plex_rating_key else "",
            )

    # If a trailer file already exists, no need to download again
    if _find_downloaded_trailer():
        return True

    # Get cookies path if available
    cookies_path = get_cookies_path()

    ydl_opts = {
        'format': f'bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}][ext=webm]+bestaudio[ext=webm]/bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}]+bestaudio/best[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}]/best',
        'outtmpl': output_filename,
        'noplaylist': True,
        'merge_output_format': TRAILER_FILE_FORMAT,
        'match_filter_func': short_videos_only,
        'default_search': 'ytsearch15',
        'extract_flat': 'in_playlist',
        'force_generic_extractor': False,
        'ignoreerrors': True,
        'quiet': not SHOW_YT_DLP_PROGRESS,
        'no_warnings': not SHOW_YT_DLP_PROGRESS,
    }
    
    # Add cookies file if available
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path
        print(f"Using cookies file: {cookies_path}")

    # Merge custom yt-dlp options from config
    if YT_DLP_CUSTOM_OPTIONS:
        custom_opts = parse_ytdlp_options(YT_DLP_CUSTOM_OPTIONS)
        # Merge, with custom options taking precedence
        for key, value in custom_opts.items():
            if key == 'extractor_args' and key in ydl_opts:
                # Merge extractor_args dictionaries
                for service, args in value.items():
                    if service in ydl_opts['extractor_args']:
                        ydl_opts['extractor_args'][service].update(args)
                    else:
                        ydl_opts['extractor_args'][service] = args
            else:
                ydl_opts[key] = value

    def verify_title_match(video_title, movie_title, year):
        """
        Verify that the video title is a valid match for the movie.
        Year is preferred but not a hard requirement — official trailers on YouTube
        often omit the year (e.g., 'Outcome — Official Trailer | Apple TV+').
        When the year is missing, the title must be specific enough and contain 'trailer'.
        """
        import re
        video_title_lower = video_title.lower()
        movie_title_lower = movie_title.lower()
        year_str = str(year)
        has_year = year_str in video_title_lower

        sanitized_movie = re.sub(r'[^\w\s]', '', movie_title_lower).strip()
        sanitized_video = re.sub(r'[^\w\s]', '', video_title_lower).strip()

        # --- Levels 1-5: With year present (strongest matches) ---
        if has_year:
            # Level 1: Full title + year (standalone match to avoid e.g. "Burden of Dreams" matching "Dreams")
            if is_standalone_title_match(movie_title_lower, video_title_lower):
                return True

            # Level 2: Colon-split parts all present + year
            movie_title_parts = movie_title_lower.split(':')
            if len(movie_title_parts) > 1:
                if all(part.strip() in video_title_lower for part in movie_title_parts):
                    return True

            # Level 3: Sanitized comparison + year (standalone match)
            if is_standalone_title_match(sanitized_movie, sanitized_video):
                return True

            # Level 4: First 70% of long titles + year
            if len(movie_title_lower) > 20:
                partial_title = movie_title_lower[:int(len(movie_title_lower) * 0.7)]
                if partial_title in video_title_lower:
                    return True

            # Level 5: Word-overlap >= 80% + year
            movie_words = set(sanitized_movie.split())
            video_words = set(sanitized_video.split())
            stopwords = {'the', 'a', 'an', 'of', 'and', 'in', 'to', 'for', 'is', 'on', 'at'}
            movie_significant = movie_words - stopwords
            if movie_significant and len(movie_significant) >= 2:
                overlap = movie_significant & video_words
                if len(overlap) / len(movie_significant) >= 0.8:
                    return True

        # --- Levels 6-7: Without year (relaxed, require 'trailer' + specific title) ---
        has_trailer_keyword = 'trailer' in video_title_lower
        is_specific_title = len(movie_title_lower.split()) >= 3 or len(movie_title_lower) >= 15

        if has_trailer_keyword and is_specific_title:
            # Level 6: Full or sanitized title match + 'trailer' keyword (standalone match)
            if is_standalone_title_match(movie_title_lower, video_title_lower) or is_standalone_title_match(sanitized_movie, sanitized_video):
                return True

            # Level 7: Colon-split parts all present + 'trailer' keyword
            movie_title_parts = movie_title_lower.split(':')
            if len(movie_title_parts) > 1:
                if all(part.strip() in video_title_lower for part in movie_title_parts):
                    return True

        # --- Level 8: Short title without year, requires standalone match + 'trailer' ---
        if has_trailer_keyword and not is_specific_title:
            if is_standalone_title_match(movie_title_lower, video_title_lower):
                return True
            if is_standalone_title_match(sanitized_movie, sanitized_video):
                return True

        return False

    # Download logic
    if SHOW_YT_DLP_PROGRESS:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for query_idx, current_query in enumerate(search_queries):
                print(f"Searching for trailer: {current_query}")
                try:
                    info = ydl.extract_info(current_query, download=False)
                    if info and 'entries' in info:
                        entries = list(filter(None, info['entries']))

                        # Filter and score entries
                        valid_entries = []
                        for idx, video in enumerate(entries):
                            if not video:
                                continue
                            duration = video.get('duration', 0)
                            video_title = video.get('title', '')
                            print(f"Found video: {video_title} (Duration: {duration} seconds)")

                            if not duration or duration > 300:
                                print(f"Skipping video - duration {duration} seconds exceeds 5-minute limit")
                                continue
                            if not is_likely_trailer(video_title):
                                print(f"Skipping video - appears to be reaction/review/non-trailer content")
                                continue
                            video['_search_position'] = idx
                            video['_movie_year'] = movie_year
                            valid_entries.append(video)

                        # Sort by score (best candidates first)
                        valid_entries.sort(key=lambda v: score_video(v), reverse=True)

                        for video in valid_entries:
                            video_title = video.get('title', '')
                            video_score = score_video(video)
                            if not verify_title_match(video_title, movie_title, movie_year):
                                print(f"Skipping video - title doesn't match movie title (score: {video_score})")
                                continue
                            print(f"Selected trailer: {video_title} (score: {video_score})")

                            try:
                                ydl.download([video['url']])
                            except yt_dlp.utils.DownloadError as e:
                                if "has already been downloaded" in str(e):
                                    print("Trailer already exists")
                                    _track_downloaded_trailer()
                                    return True
                                print(f"Failed to download video: {str(e)}")
                                continue

                            if _find_downloaded_trailer():
                                print(f"Trailer successfully downloaded for '{movie_title} ({movie_year})'")
                                _track_downloaded_trailer()
                                return True

                        if query_idx < len(search_queries) - 1:
                            print("No match found, trying alternative search query...")
                        else:
                            print("No suitable videos found matching criteria")

                except Exception as e:
                    if _find_downloaded_trailer():
                        print(f"Trailer exists despite error: {str(e)}")
                        _track_downloaded_trailer()
                        return True
                    print(f"Unexpected error downloading trailer for '{movie_title} ({movie_year})': {str(e)}")
                    if query_idx == len(search_queries) - 1:
                        return False
            return False

    else:
        # Quiet version with minimal output
        print(f"Searching trailer for {movie_title} ({movie_year})...")
        ydl_opts['quiet'] = True
        ydl_opts['no_warnings'] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for query_idx, current_query in enumerate(search_queries):
                try:
                    info = ydl.extract_info(current_query, download=False)
                    if info and 'entries' in info:
                        entries = list(filter(None, info['entries']))

                        # Filter and score entries
                        valid_entries = []
                        for idx, video in enumerate(entries):
                            if not video:
                                continue
                            duration = video.get('duration', 0)
                            video_title = video.get('title', '')
                            if not duration or duration > 300:
                                continue
                            if not is_likely_trailer(video_title):
                                continue
                            video['_search_position'] = idx
                            video['_movie_year'] = movie_year
                            valid_entries.append(video)

                        valid_entries.sort(key=lambda v: score_video(v), reverse=True)

                        for video in valid_entries:
                            if not verify_title_match(video.get('title', ''), movie_title, movie_year):
                                continue
                            try:
                                ydl.download([video['url']])
                            except yt_dlp.utils.DownloadError as e:
                                if "has already been downloaded" in str(e):
                                    print_colored("Trailer already exists", 'green')
                                    _track_downloaded_trailer()
                                    return True
                                continue

                            if _find_downloaded_trailer():
                                print_colored("Trailer download successful", 'green')
                                _track_downloaded_trailer()
                                return True
                except Exception as e:
                    if _find_downloaded_trailer():
                        print_colored("Trailer download successful", 'green')
                        _track_downloaded_trailer()
                        return True
                    if query_idx == len(search_queries) - 1:
                        print_colored("Trailer download failed. Turn on SHOW_YT_DLP_PROGRESS for more info", 'red')
                        return False
            return False
    
    # Clean up any partial downloads
    cleanup_trailer_files(sanitized_title, movie_year, trailers_folder)
    return False

# Main processing
start_time = datetime.now()

# Process each movie library
for library_config in MOVIE_LIBRARIES:
    library_name = library_config['name']
    library_genres_to_skip = library_config.get('genres_to_skip', [])
    
    print_colored(f"\nChecking your {library_name} library for missing trailers", 'blue')
    
    # Conditionally fetch movies based on USE_LABELS setting
    if USE_LABELS:
        # Get movies without MTDfP label using filters
        filters = {
            'and': [
                {'label!': 'MTDfP'}   # Movies without MTDfP label
            ]
        }
        all_movies = plex.library.section(library_name).search(filters=filters)
        print_colored(f"Found {len(all_movies)} movies without MTDfP label", 'blue')
    else:
        # Get all movies (v1 behavior)
        all_movies = plex.library.section(library_name).all()

    total_movies = len(all_movies)

    for index, movie in enumerate(all_movies, start=1):
        print(f"Checking movie {index}/{total_movies}: {movie.title}")
        movie.reload()

        # If it has any skip-genres, skip it
        movie_genres = [genre.tag.lower() for genre in (movie.genres or [])]
        if any(skip_genre.lower() in movie_genres for skip_genre in library_genres_to_skip):
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
            already_has_trailer = has_local_trailer(normalize_path_for_docker(movie.locations[0]))

        if not already_has_trailer:
            # No trailer found
            if DOWNLOAD_TRAILERS:
                movie_path = normalize_path_for_docker(movie.locations[0])
                success = download_trailer(movie.title, movie.year, movie_path,
                                          trailer_tracker=_trailer_tracker, plex_rating_key=movie.ratingKey)
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
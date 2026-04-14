import os
import sys
import re
import yaml
from plexapi.server import PlexServer
import yt_dlp
import urllib.parse
from datetime import datetime
import shlex
import subprocess
from pathlib import Path


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
    # Fallback to old single library format for backward compatibility
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

_BLOCKED_YTDLP_OPTS = {
    'exec', 'exec_before_dl', 'exec_before_download',
    'output', 'outtmpl', 'paths', 'batch_file',
    'cookies', 'cookiefile', 'cookies_from_browser', 'cookiesfrombrowser',
    'download_archive', 'config_locations', 'config_location',
    'plugin_dirs', 'write_pages', 'print_to_file',
}


def parse_ytdlp_options(options_list):
    """Parse command-line style yt-dlp options into a dictionary."""
    parsed_opts = {}

    for option_str in options_list:
        parts = shlex.split(option_str)

        for i, part in enumerate(parts):
            if not part.startswith('--'):
                continue

            key = part[2:].replace('-', '_')
            if key in _BLOCKED_YTDLP_OPTS:
                print(f"[Security] Blocked dangerous yt-dlp option: --{part[2:]}")
                continue
            
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

# Connect to Plex
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

# Print configuration
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
if YT_DLP_CUSTOM_OPTIONS:
    print(f"YT_DLP_CUSTOM_OPTIONS: {', '.join(YT_DLP_CUSTOM_OPTIONS)}")
if IS_DOCKER:
    print(f"Running in: {GREEN}Docker Container{RESET}")

# Initialize trailer tracker for dashboard carousel
try:
    from Modules.trailer_tracker import TrailerTracker
except ImportError:
    from trailer_tracker import TrailerTracker
_trailer_tracker = TrailerTracker()

# Lists to store the status of trailer downloads
shows_with_downloaded_trailers = {}
shows_download_errors = []
shows_permission_errors = []
shows_skipped = []
shows_missing_trailers = []

NEGATIVE_TITLE_KEYWORDS = [
    'reaction', 'react', 'review', 'behind the scenes',
    'making of', 'breakdown', 'explained', 'analysis', 'fan made',
    'fan-made', 'parody', 'spoof', 'honest trailer', 'honest trailers',
    'everything wrong', 'pitch meeting', 'recap', 'summary',
    'cast interview', 'press tour', 'red carpet',
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

def is_standalone_title_match(show_title_lower, video_title_lower):
    """Check if show title appears as standalone phrase, not part of a longer show name."""
    import re
    pattern = r'\b' + re.escape(show_title_lower) + r'\b'
    match = re.search(pattern, video_title_lower)
    if not match:
        return False

    # For short titles (1-2 words), reject if significant words precede the title
    # (e.g., "Burden of" before "Dreams" means it's a different title)
    show_words = show_title_lower.split()
    if len(show_words) <= 2:
        prefix = video_title_lower[:match.start()].strip()
        if prefix:
            prefix = re.sub(r'[|\-:!]', ' ', prefix).strip()
            prefix_words = prefix.split()
            significant = [w for w in prefix_words if w not in TRAILER_NOISE_WORDS and len(w) > 2]
            if significant:
                return False
    return True

LANGUAGE_KEYWORDS = {
    'german': ['deutsch', 'german', 'auf deutsch', 'de'],
    'french': ['français', 'francais', 'french', 'vf', 'vostfr', 'fr'],
    'spanish': ['español', 'espanol', 'spanish', 'castellano', 'es'],
    'italian': ['italiano', 'italian', 'it'],
    'japanese': ['日本語', 'japanese', 'jp', 'ja'],
    'korean': ['한국어', 'korean', 'ko'],
    'portuguese': ['português', 'portugues', 'portuguese', 'pt', 'dublado'],
    'russian': ['русский', 'russian', 'ru'],
    'chinese': ['中文', 'chinese', 'zh'],
    'english': ['english', 'en'],
}

LANGUAGE_CODES = {
    'german': 'de', 'french': 'fr', 'spanish': 'es', 'italian': 'it',
    'japanese': 'ja', 'korean': 'ko', 'portuguese': 'pt', 'russian': 'ru',
    'chinese': 'zh', 'english': 'en',
}


def _matches_language_keyword(text, keywords):
    """Check if any keyword matches in text, using word boundaries for short keywords."""
    for kw in keywords:
        if len(kw) <= 3:
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                return True
        else:
            if kw in text:
                return True
    return False


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
    show_year = str(video.get('_movie_year', ''))
    if show_year:
        years_in_title = re.findall(r'\b((?:19|20)\d{2})\b', title)
        if years_in_title and show_year not in years_in_title:
            score -= 3

    # Language bonus/penalty: strongly prefer videos matching the user's preferred language
    if PREFERRED_LANGUAGE.lower() != 'original':
        lang_kws = LANGUAGE_KEYWORDS.get(PREFERRED_LANGUAGE.lower(), [PREFERRED_LANGUAGE.lower()])
        matches_preferred = _matches_language_keyword(title, lang_kws) or _matches_language_keyword(channel, lang_kws)

        if matches_preferred:
            score += 25
        else:
            # Penalty if video explicitly mentions a different language
            other_lang_kws = []
            for lang, kws in LANGUAGE_KEYWORDS.items():
                if lang != PREFERRED_LANGUAGE.lower():
                    other_lang_kws.extend(kw for kw in kws if len(kw) >= 4)
            if _matches_language_keyword(title, other_lang_kws):
                score -= 15

    return score


def _video_matches_language(video_title, video_channel=''):
    """Check if a video's title or channel contains keywords for the preferred language."""
    if PREFERRED_LANGUAGE.lower() == 'original':
        return False
    lang_kws = LANGUAGE_KEYWORDS.get(PREFERRED_LANGUAGE.lower(), [PREFERRED_LANGUAGE.lower()])
    title_lower = video_title.lower()
    channel_lower = video_channel.lower()
    return _matches_language_keyword(title_lower, lang_kws) or _matches_language_keyword(channel_lower, lang_kws)


def _rename_with_lang_tag(filepath, lang_code):
    """Rename a downloaded trailer to include the language tag."""
    import re as _re
    directory = os.path.dirname(filepath)
    name, ext = os.path.splitext(os.path.basename(filepath))
    if not name.endswith('-trailer'):
        return filepath
    prefix = name[:-len('-trailer')]
    # Check if already has a language code
    parts = prefix.rsplit('.', 1)
    if len(parts) == 2 and parts[1] in LANGUAGE_CODES.values():
        return filepath  # Already has a lang tag
    # Insert lang code before -trailer (after resolution label if present)
    new_name = f"{prefix}.{lang_code}-trailer{ext}"
    new_path = os.path.join(directory, new_name)
    try:
        os.rename(filepath, new_path)
        print(f"Added language tag: {os.path.basename(filepath)} -> {os.path.basename(new_path)}")
        return new_path
    except OSError as e:
        print(f"Failed to add language tag: {e}")
        return filepath


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

def cleanup_trailer_files(show_title, trailers_folder):
    """
    Remove any leftover partial download files that match our trailer filename prefix
    (excluding the final output format).
    """
    lang_code = LANGUAGE_CODES.get(PREFERRED_LANGUAGE.lower(), '')
    lang_tag = f".{lang_code}" if lang_code else ""
    prefix = f"{show_title}{lang_tag}-trailer."
    for file in os.listdir(trailers_folder):
        if file.startswith(prefix) and not file.endswith(f".{TRAILER_FILE_FORMAT}"):
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
    # If folder doesn't exist or is inaccessible, return False
    if not os.path.isdir(show_directory):
        print(f"Warning: Cannot access directory: {show_directory}")
        return False

    try:
        contents = os.listdir(show_directory)
    except OSError as e:
        print(f"Warning: Error listing directory '{show_directory}': {e}")
        return False

    # 1) Look for any '*-trailer' file
    for f in contents:
        if f.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.wmv')):
            name_without_ext, _ = os.path.splitext(f.lower())
            if name_without_ext.endswith("-trailer"):
                return True

    # 2) Check for a 'Trailers' subfolder with at least one video file
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

def _classify_resolution(width, height):
    """Classify resolution using both width and height.

    For cinematic aspect ratios (e.g. 1280x550) the height alone
    under-reports the quality.  We derive an effective height from the
    width assuming 16:9 and take the higher of the two values.
    """
    effective_height = max(height, int(width * 9 / 16))
    for threshold, label in [(2160, "2160p"), (1440, "1440p"), (1080, "1080p"),
                             (720, "720p"), (480, "480p"), (360, "360p")]:
        if effective_height >= threshold:
            return label
    return f"{height}p"


def _rename_with_resolution(filepath):
    """Probe a downloaded trailer's resolution and rename the file to include it."""
    import re as _re
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        dims = result.stdout.strip().split(',')
        if len(dims) != 2:
            return filepath
        width, height = int(dims[0]), int(dims[1])
        res_label = _classify_resolution(width, height)
    except Exception:
        return filepath

    directory = os.path.dirname(filepath)
    name, ext = os.path.splitext(os.path.basename(filepath))
    if not name.endswith('-trailer'):
        return filepath

    # Skip if resolution is already in the filename
    if _re.search(r'\.\d{3,4}p[.\-]', name):
        return filepath

    prefix = name[:-len('-trailer')]

    # Insert resolution before language code (if present) and -trailer
    parts = prefix.rsplit('.', 1)
    if len(parts) == 2 and parts[1] in LANGUAGE_CODES.values():
        new_name = f"{parts[0]}.{res_label}.{parts[1]}-trailer{ext}"
    else:
        new_name = f"{prefix}.{res_label}-trailer{ext}"

    new_path = os.path.join(directory, new_name)
    try:
        os.rename(filepath, new_path)
        print(f"Renamed trailer: {os.path.basename(filepath)} -> {new_name}")
        return new_path
    except OSError as e:
        print(f"Failed to rename trailer: {e}")
        return filepath


def download_trailer(show_title, show_year, show_directory, trailer_tracker=None, plex_rating_key=None):
    """
    Attempt to download a trailer for the TV show using YouTube search.
    """
    # Sanitize show_title to remove or replace problematic characters
    sanitized_title = show_title.replace(":", " -")

    # Prepare search queries — primary and fallback with different phrasing
    language_suffix = f" {PREFERRED_LANGUAGE}" if PREFERRED_LANGUAGE.lower() != "original" else ""
    search_queries = [
        f"ytsearch15:{show_title} {show_year} TV show official trailer{language_suffix}",
        f"ytsearch15:{show_title} trailer {show_year} TV series{language_suffix}",
        f"ytsearch15:{show_title} {show_year} series trailer{language_suffix}",
    ]
    search_query = search_queries[0]

    # Build the trailers directory
    trailers_directory = os.path.join(show_directory, 'Trailers')

    # Create or reuse the folder
    os.makedirs(trailers_directory, exist_ok=True)

    # Language code — will only be applied to the filename AFTER download
    # if the video title actually matches the preferred language
    lang_code = LANGUAGE_CODES.get(PREFERRED_LANGUAGE.lower(), '')

    output_filename = os.path.join(
        trailers_directory,
        f"{sanitized_title}-trailer.%(ext)s"
    )
    final_trailer_filename = os.path.join(
        trailers_directory,
        f"{sanitized_title}-trailer.{TRAILER_FILE_FORMAT}"
    )

    trailer_base_name = f"{sanitized_title}-trailer"
    VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')

    def _find_downloaded_trailer():
        """Check if any trailer file exists (any video extension) and return its path.
        """
        if os.path.exists(final_trailer_filename):
            return final_trailer_filename
        try:
            for f in os.listdir(trailers_directory):
                name, ext = os.path.splitext(f)
                if ext.lower() in VIDEO_EXTENSIONS and name.endswith('-trailer') and name.startswith(sanitized_title):
                    return os.path.join(trailers_directory, f)
        except OSError:
            pass
        return None

    def _track_downloaded_trailer(video_title_for_lang=None, video_channel_for_lang=None):
        """Rename the trailer to include resolution and language tag, then record it in the tracker."""
        trailer_path = _find_downloaded_trailer()
        if trailer_path:
            trailer_path = _rename_with_resolution(trailer_path)
            # Apply language tag only if the video actually matches the preferred language
            if lang_code and video_title_for_lang and _video_matches_language(
                    video_title_for_lang, video_channel_for_lang or ''):
                trailer_path = _rename_with_lang_tag(trailer_path, lang_code)
        if trailer_tracker and trailer_path:
            trailer_tracker.add_trailer(
                file_path=trailer_path,
                title=show_title,
                year=str(show_year) if show_year else "",
                media_type="show",
                plex_rating_key=str(plex_rating_key) if plex_rating_key else "",
                poster_url=f"/api/plex/poster/{plex_rating_key}" if plex_rating_key else "",
            )

    # If there's already a trailer file, skip download
    if _find_downloaded_trailer():
        return True

    def verify_title_match(video_title, show_title, year):
        """
        Verify that the video title is a valid match for the TV show.
        Uses the year from Plex metadata. Year is preferred but not always
        required since YouTube trailer titles often omit the year.
        """
        import re
        video_title_lower = video_title.lower()
        year_str = str(year) if year else None

        # Extract base title (strip parenthesized year if present in show title)
        base_title = re.sub(r'\s*\(\d{4}\)\s*', '', show_title).lower().strip()
        sanitized_base = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', base_title)).strip()
        sanitized_video = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', video_title_lower)).strip()

        if year_str:
            has_year = year_str in video_title_lower

            # Level 1: Base title + year both present (standalone match)
            if is_standalone_title_match(base_title, video_title_lower) and has_year:
                return True

            # Level 2: Sanitized base title + year (standalone match)
            if is_standalone_title_match(sanitized_base, sanitized_video) and has_year:
                return True

            # Level 3: Colon-split parts + year
            parts = base_title.split(':')
            if len(parts) > 1 and all(p.strip() in video_title_lower for p in parts) and has_year:
                return True

            # Level 4 (relaxed): Base title present + "trailer" in video title, no year required
            # Only allow if the title is specific enough to avoid false positives
            if is_standalone_title_match(base_title, video_title_lower) and 'trailer' in video_title_lower:
                if len(base_title.split()) >= 3 or len(base_title) >= 15:
                    return True

            # Level 5 (relaxed): Sanitized match + "trailer", no year required
            if is_standalone_title_match(sanitized_base, sanitized_video) and 'trailer' in video_title_lower:
                if len(base_title.split()) >= 3 or len(base_title) >= 15:
                    return True

            # Level 6: Short title + trailer keyword + standalone match (no year required)
            if 'trailer' in video_title_lower:
                if is_standalone_title_match(base_title, video_title_lower):
                    return True
                if is_standalone_title_match(sanitized_base, sanitized_video):
                    return True

            return False

        # No year available — more lenient matching (standalone)
        if is_standalone_title_match(base_title, video_title_lower):
            return True
        if is_standalone_title_match(sanitized_base, sanitized_video):
            return True
        parts = base_title.split(':')
        if len(parts) > 1 and all(p.strip() in video_title_lower for p in parts):
            return True

        return False

    # Get cookies path if available
    cookies_path = get_cookies_path()

    ydl_opts = {
        'format': f'bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}][ext=webm]+bestaudio[ext=webm]/bestvideo[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}]+bestaudio/best[height<={TRAILER_RESOLUTION_MAX}][height>={TRAILER_RESOLUTION_MIN}]',
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
                            video['_movie_year'] = show_year
                            valid_entries.append(video)

                        # Sort by score (best candidates first)
                        valid_entries.sort(key=lambda v: score_video(v), reverse=True)

                        for video in valid_entries:
                            video_title = video.get('title', '')
                            video_score = score_video(video)
                            if not verify_title_match(video_title, show_title, show_year):
                                print(f"Skipping video - title doesn't match show title (score: {video_score})")
                                continue
                            print(f"Selected trailer: {video_title} (score: {video_score})")

                            video_channel = video.get('channel', '') or video.get('uploader', '') or ''
                            try:
                                ydl.download([video['url']])
                            except yt_dlp.utils.DownloadError as e:
                                if "has already been downloaded" in str(e):
                                    print("Trailer already exists")
                                    _track_downloaded_trailer(video_title, video_channel)
                                    return True
                                print(f"Failed to download video: {str(e)}")
                                continue

                            if _find_downloaded_trailer():
                                print(f"Trailer successfully downloaded for '{show_title}'")
                                _track_downloaded_trailer(video_title, video_channel)
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
                    print(f"Unexpected error downloading trailer for '{show_title}': {str(e)}")
                    if query_idx == len(search_queries) - 1:
                        return False
            return False

    else:
        # Quiet version with minimal output
        print(f"Searching trailer for {show_title}...")
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
                            video['_movie_year'] = show_year
                            valid_entries.append(video)

                        valid_entries.sort(key=lambda v: score_video(v), reverse=True)

                        for video in valid_entries:
                            if not verify_title_match(video.get('title', ''), show_title, show_year):
                                continue
                            video_title_q = video.get('title', '')
                            video_channel_q = video.get('channel', '') or video.get('uploader', '') or ''
                            try:
                                ydl.download([video['url']])
                            except yt_dlp.utils.DownloadError as e:
                                if "has already been downloaded" in str(e):
                                    print_colored("Trailer already exists", 'green')
                                    _track_downloaded_trailer(video_title_q, video_channel_q)
                                    return True
                                continue

                            if _find_downloaded_trailer():
                                print_colored("Trailer download successful", 'green')
                                _track_downloaded_trailer(video_title_q, video_channel_q)
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
    cleanup_trailer_files(sanitized_title, trailers_directory)
    return False

# Main processing
start_time = datetime.now()

# Process each TV library
for library_config in TV_LIBRARIES:
    library_name = library_config['name']
    library_genres_to_skip = library_config.get('genres_to_skip', [])
    
    print_colored(f"\nChecking your {library_name} library for missing trailers", 'blue')
    
    # Get the TV section for this library
    tv_section = plex.library.section(library_name)
    
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
        if any(skip_genre.lower() in show_genres for skip_genre in library_genres_to_skip):
            print(f"Skipping '{show.title}' (Genres match skip list: {', '.join(show_genres)})")
            shows_skipped.append(show.title)
            continue

        # If CHECK_PLEX_PASS_TRAILERS is True => check Plex extras
        # If False => check only local trailer files
        if CHECK_PLEX_PASS_TRAILERS:
            trailers = [
                extra for extra in show.extras()
                if extra.type == 'clip' and extra.subtype == 'trailer'
            ]
            already_has_trailer = bool(trailers)
        else:
            already_has_trailer = has_local_trailer(normalize_path_for_docker(show.locations[0]))

        if not already_has_trailer:
            # No trailer found
            if DOWNLOAD_TRAILERS:
                show_directory = normalize_path_for_docker(show.locations[0])
                try:
                    success = download_trailer(show.title, show.year, show_directory,
                                              trailer_tracker=_trailer_tracker, plex_rating_key=show.ratingKey)
                except PermissionError as e:
                    print(f"Permission denied for '{show.title}': {e}")
                    success = False
                    if show.title not in shows_permission_errors:
                        shows_permission_errors.append(show.title)
                except OSError as e:
                    print(f"OS error for '{show.title}': {e}")
                    success = False
                    if show.title not in shows_permission_errors:
                        shows_permission_errors.append(show.title)
                if success:
                    folder_name = os.path.basename(show_directory)
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

if shows_permission_errors:
    print("\n")
    print_colored("TV Shows skipped due to permission errors:", 'red')
    print("(Check that the volume is mapped and has the correct permissions)")
    for show in sorted(set(shows_permission_errors)):
        print(show)

# If none are missing, none failed, and none downloaded, everything is good!
if not shows_missing_trailers and not shows_download_errors and not shows_with_downloaded_trailers and not shows_permission_errors:
    print("\n")
    print(f"{GREEN}No missing trailers!{RESET}")

end_time = datetime.now()
run_time = str(end_time - start_time).split('.')[0]
print("\n")
print_colored("Run Time: ", 'blue', end="")
print(run_time)
import os
import subprocess
import sys
import yaml
import requests
from plexapi.server import PlexServer
from datetime import datetime
import time
import signal

VERSION= "2026.04.03"

_tracker = None  # Global trailer tracker instance

class PlexConnectionError(Exception):
    """Raised when Plex credentials are missing or connection fails."""
    pass

# Get the directory of the script being executed
script_dir = os.path.dirname(os.path.abspath(__file__))

# Check if running in Docker
IS_DOCKER = os.environ.get('IS_DOCKER', 'false').lower() == 'true'

# Resolve paths for requirements, config, and module scripts
requirements_path = os.path.join(script_dir, "requirements.txt")

if IS_DOCKER:
    config_path = "/config/config.yml"
else:
    config_path = os.path.join(script_dir, "config.yml")

movies_script_path = os.path.join(script_dir, "Modules", "Movies.py")
tv_shows_script_path = os.path.join(script_dir, "Modules", "TV.py")

# ANSI color codes
GREEN = '\033[32m'
ORANGE = '\033[33m'
RED = '\033[31m'
RESET = '\033[0m'

# Signal handler for graceful shutdown
def signal_handler(signum, frame):
    print(f"\n{ORANGE}Received shutdown signal. Exiting gracefully...{RESET}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Version check
def check_version():
    try:
        response = requests.get("https://github.com/netplexflix/Missing-Trailer-Downloader-For-Plex/releases/latest")
        if response.status_code == 200:
            latest_version = response.url.split('/')[-1]
            current_version = VERSION
            if latest_version > current_version:
                print(f"{ORANGE}A newer version ({latest_version}) is available!{RESET}")
            else:
                print("You are using the latest version.")
        else:
            print(f"{RED}Failed to check for updates.{RESET}")
    except Exception as e:
        print(f"{RED}Error checking version: {e}{RESET}")


# Check yt-dlp version
def check_ytdlp_version():
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            print(f"yt-dlp version: {GREEN}{version}{RESET}")
        else:
            print(f"{RED}yt-dlp: Error getting version{RESET}")
    except FileNotFoundError:
        print(f"{RED}yt-dlp: Not found - Please install yt-dlp{RESET}")
    except Exception as e:
        print(f"{RED}yt-dlp: Error checking version: {e}{RESET}")


## Check requirements
def check_requirements():
    print("\nChecking requirements:")
    try:
        # Use the resolved path for requirements.txt
        with open(requirements_path, "r", encoding="utf-8") as req_file:
            requirements = req_file.readlines()

        unmet_requirements = []
        for req in requirements:
            req = req.strip()
            if not req or req.startswith('#'):  # Skip empty lines and comments
                continue
                
            try:
                # Extract base package name (remove extras like [default])
                base_req = req
                if '[' in req:
                    base_pkg_name = req.split('[')[0].strip()
                    # Keep the full requirement for installation
                    full_req = req
                else:
                    base_pkg_name = req.split('>=')[0].split('==')[0].strip()
                    full_req = req
                
                # Handle version specifiers
                if ">=" in base_req:
                    pkg_parts = base_req.split(">=")
                    pkg_name = pkg_parts[0].split('[')[0].strip()
                    required_version = pkg_parts[1].strip()
                    comparison_operator = ">="
                elif "==" in base_req:
                    pkg_parts = base_req.split("==")
                    pkg_name = pkg_parts[0].split('[')[0].strip()
                    required_version = pkg_parts[1].strip()
                    comparison_operator = "=="
                else:
                    # For packages without version specification
                    pkg_name = base_pkg_name
                    required_version = None
                    comparison_operator = None

                # Use text=True and errors="replace" for pip show output
                # Use base package name without extras for pip show
                installed_info = subprocess.check_output(
                    [sys.executable, "-m", "pip", "show", pkg_name],
                    text=True,
                    errors="replace"
                )
                installed_version = installed_info.split("Version: ")[1].split("\n")[0]

                if not required_version:
                    print(f"{base_pkg_name}: {GREEN}OK{RESET}")
                elif comparison_operator == ">=":
                    if installed_version >= required_version:
                        print(f"{base_pkg_name}: {GREEN}OK{RESET}")
                    else:
                        print(f"{base_pkg_name}: {ORANGE}Upgrade needed{RESET}")
                        unmet_requirements.append(full_req)
                elif comparison_operator == "==":
                    if installed_version == required_version:
                        print(f"{base_pkg_name}: {GREEN}OK{RESET}")
                    else:
                        print(f"{base_pkg_name}: {ORANGE}Version mismatch{RESET}")
                        unmet_requirements.append(full_req)
                
            except (IndexError, subprocess.CalledProcessError) as e:
                display_name = base_pkg_name if 'base_pkg_name' in locals() else req
                print(f"{display_name}: {RED}Missing or error: {str(e)}{RESET}")
                unmet_requirements.append(full_req if 'full_req' in locals() else req)

        if unmet_requirements:
            if IS_DOCKER:
                sys.exit(f"{RED}Docker container has unmet requirements. Please rebuild the image.{RESET}")
            else:
                answer = input("Install requirements? (y/n): ").strip().lower()
                if answer == "y":
                    subprocess.run([sys.executable, "-m", "pip", "install", "-r", requirements_path])
                else:
                    sys.exit(f"{RED}Script ended due to unmet requirements.{RESET}")

    except Exception as e:
        sys.exit(f"{RED}Error checking requirements: {e}{RESET}")


# Check Plex connection
def check_plex_connection(config):
    PLEX_URL = config.get("PLEX_URL")
    PLEX_TOKEN = config.get("PLEX_TOKEN")
    if not PLEX_URL or not PLEX_TOKEN or PLEX_TOKEN == "YOUR_PLEX_TOKEN":
        msg = "Plex credentials not configured. Please set your Plex URL and Token via the web UI (port 2121) or directly in /config/config.yml, then restart the container or trigger a manual run."
        print(f"Connection to Plex: {RED}{msg}{RESET}")
        raise PlexConnectionError(msg)
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        print(f"Connection to Plex: {GREEN}Successful{RESET}")
        return plex
    except Exception:
        msg = "Plex connection failed. Please verify your Plex URL and Token via the web UI (port 2121) or directly in /config/config.yml, then restart the container or trigger a manual run."
        print(f"Connection to Plex: {RED}{msg}{RESET}")
        raise PlexConnectionError(msg)


# Check libraries
def check_libraries(config, plex):
    errors = []
    
    # Check movie libraries
    movie_libraries = config.get("MOVIE_LIBRARIES", [])
    if not movie_libraries:
        # Fallback to old single library format for backward compatibility
        movie_library_name = config.get("MOVIE_LIBRARY_NAME")
        if movie_library_name:
            movie_libraries = [{"name": movie_library_name, "genres_to_skip": config.get("MOVIE_GENRES_TO_SKIP", [])}]
    
    for library in movie_libraries:
        library_name = library.get("name")
        try:
            plex.library.section(library_name)
            print(f"Movie Library ({library_name}): {GREEN}OK{RESET}")
        except Exception:
            errors.append(f"Movie Library ({library_name}): {RED}Not Found - Please verify library name in config.yml{RESET}")
    
    # Check TV libraries
    tv_libraries = config.get("TV_LIBRARIES", [])
    if not tv_libraries:
        # Fallback to old single library format for backward compatibility
        tv_library_name = config.get("TV_LIBRARY_NAME")
        if tv_library_name:
            tv_libraries = [{"name": tv_library_name, "genres_to_skip": config.get("TV_GENRES_TO_SKIP", [])}]
    
    for library in tv_libraries:
        library_name = library.get("name")
        try:
            plex.library.section(library_name)
            print(f"TV Library ({library_name}): {GREEN}OK{RESET}")
        except Exception:
            errors.append(f"TV Library ({library_name}): {RED}Not Found - Please verify library name in config.yml{RESET}")

    if errors:
        for error in errors:
            print(error)
        sys.exit(f"{RED}Library check failed.{RESET}")


# Launch scripts based on LAUNCH_METHOD
def launch_scripts(config):
    LAUNCH_METHOD = config.get("LAUNCH_METHOD", "0")
    start_time = datetime.now()

    # Get library names for display
    movie_libraries = config.get("MOVIE_LIBRARIES", [])
    tv_libraries = config.get("TV_LIBRARIES", [])
    
    # Fallback to old single library format for backward compatibility
    if not movie_libraries:
        movie_library_name = config.get("MOVIE_LIBRARY_NAME")
        if movie_library_name:
            movie_libraries = [{"name": movie_library_name}]
    
    if not tv_libraries:
        tv_library_name = config.get("TV_LIBRARY_NAME")
        if tv_library_name:
            tv_libraries = [{"name": tv_library_name}]

    # In Docker, always use the configured LAUNCH_METHOD
    if IS_DOCKER:
        choice = LAUNCH_METHOD if LAUNCH_METHOD != "0" else "3"  # Default to both in Docker
    else:
        if LAUNCH_METHOD == "0":
            print("\nChoose an option:")
            if movie_libraries:
                movie_names = [lib["name"] for lib in movie_libraries]
                print(f"1 = Movie libraries ({', '.join(movie_names)})")
            if tv_libraries:
                tv_names = [lib["name"] for lib in tv_libraries]
                print(f"2 = TV Show libraries ({', '.join(tv_names)})")
            print("3 = Both consecutively")
            choice = input("Enter your choice: ").strip()
        else:
            choice = LAUNCH_METHOD

    def _run_script(script_path):
        """Run a module script, streaming its output through our stdout/stderr."""
        proc = subprocess.Popen(
            [sys.executable, "-u", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            errors="replace",
        )
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()

    if choice == "1":
        print("\nLaunching Movies script...")
        _run_script(movies_script_path)
    elif choice == "2":
        print("\nLaunching TV Shows script...")
        _run_script(tv_shows_script_path)
    elif choice == "3":
        print("\nLaunching Movies script...")
        _run_script(movies_script_path)
        print("\nLaunching TV Shows script...")
        _run_script(tv_shows_script_path)

        # Calculate and print total runtime
        end_time = datetime.now()
        total_runtime = end_time - start_time
        print(f"\nTotal runtime: {str(total_runtime).split('.')[0]}")
    else:
        print(f"{RED}Invalid choice. Exiting...{RESET}")


def run_once():
    """Run the script once"""
    # Print title
    print(f"Missing Trailer Downloader for Plex {VERSION}")

    # Always check for latest version
    check_version()
    
    # Check yt-dlp version
    check_ytdlp_version()

    # Load config before deciding on requirements check
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except Exception as e:
        sys.exit(f"{RED}Failed to load config.yml: {e}{RESET}")

    # Only check requirements if LAUNCH_METHOD is "0" and not in Docker
    LAUNCH_METHOD = config.get("LAUNCH_METHOD", "0")
    if LAUNCH_METHOD == "0" and not IS_DOCKER:
        check_requirements()

    # Proceed with the rest of your flow
    plex = check_plex_connection(config)
    check_libraries(config, plex)
    launch_scripts(config)

def run_scheduled(sched_state=None):
    """Run the script on a schedule in Docker"""
    print(f"{GREEN}Starting Missing Trailer Downloader for Plex in scheduled mode{RESET}")

    # Get schedule from environment or config
    schedule_hours = int(os.environ.get('SCHEDULE_HOURS', '24'))

    print(f"Will run every {schedule_hours} hours")

    if sched_state is not None:
        sched_state.set_schedule(schedule_hours)

    # Track consecutive failures
    consecutive_failures = 0
    max_consecutive_failures = 3

    # Run immediately on start
    print(f"\n{'=' * 60}")
    print(f"MTDP - Initial run on container start")
    print(f"{'=' * 60}")
    if sched_state is not None:
        sched_state.set_status("running")
    try:
        run_once()
        consecutive_failures = 0
        if sched_state is not None:
            sched_state.set_last_run(datetime.now())
    except PlexConnectionError as e:
        print(f"{ORANGE}Waiting for valid Plex credentials. The web UI is available on port 2121.{RESET}")
        if sched_state is not None:
            sched_state.set_status("error", str(e))
    except (KeyboardInterrupt, SystemExit) as e:
        consecutive_failures += 1
        print(f"{RED}Initial run failed: {e}{RESET}")
        if sched_state is not None:
            sched_state.set_status("error", str(e))
            sched_state.set_last_run(datetime.now())
    except Exception as e:
        consecutive_failures += 1
        print(f"{RED}Initial run failed: {e}{RESET}")
        if sched_state is not None:
            sched_state.set_status("error", str(e))
            sched_state.set_last_run(datetime.now())

    # Schedule loop
    while True:
        # Stopped state: wait until resumed or run-now
        if sched_state is not None and sched_state.is_stopped():
            sched_state.set_status("stopped")
            print(f"\n{'=' * 60}")
            print("MTDP - Scheduler paused by user")
            print(f"{'=' * 60}\n")
            sched_state._wake_event.wait()
            sched_state._wake_event.clear()
            if sched_state.is_run_requested():
                sched_state.clear_run_request()
            elif sched_state.is_stopped():
                continue
            else:
                continue
        else:
            # Calculate next run time
            next_run = datetime.now() + __import__('datetime').timedelta(hours=schedule_hours)
            wait_seconds = schedule_hours * 3600

            if sched_state is not None:
                sched_state.set_next_run(next_run)
                sched_state.set_status("idle")

            print(f"\n{'=' * 60}")
            print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            h = int(wait_seconds // 3600)
            m = int((wait_seconds % 3600) // 60)
            print(f"Waiting {h}h {m}m...")
            print(f"{'=' * 60}\n")

            # Interruptible wait
            if sched_state is not None:
                woken = sched_state._wake_event.wait(timeout=max(0, wait_seconds))
                sched_state._wake_event.clear()

                if sched_state.is_stopped():
                    continue
                if sched_state.is_run_requested():
                    sched_state.clear_run_request()
                elif not woken:
                    pass  # Timeout reached, time for scheduled run
                else:
                    continue
            else:
                time.sleep(wait_seconds)

        # Execute run
        print(f"\n{'=' * 60}")
        print(f"MTDP - Scheduled run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 60}")
        if sched_state is not None:
            sched_state.set_status("running")

        try:
            run_once()
            consecutive_failures = 0
        except PlexConnectionError as e:
            print(f"{ORANGE}Waiting for valid Plex credentials. The web UI is available on port 2121.{RESET}")
            if sched_state is not None:
                sched_state.set_status("error", str(e))
        except KeyboardInterrupt:
            print(f"\n{ORANGE}Received interrupt signal. Exiting...{RESET}")
            break
        except SystemExit as e:
            consecutive_failures += 1
            print(f"{RED}Critical error: {e}{RESET}")
            print(f"Consecutive failures: {consecutive_failures}/{max_consecutive_failures}")
            if sched_state is not None:
                sched_state.set_status("error", str(e))
            if consecutive_failures >= max_consecutive_failures:
                print(f"{RED}Maximum consecutive failures reached. Exiting...{RESET}")
                sys.exit(1)
        except Exception as e:
            consecutive_failures += 1
            print(f"{RED}Error during scheduled run: {e}{RESET}")
            print(f"Consecutive failures: {consecutive_failures}/{max_consecutive_failures}")
            if sched_state is not None:
                sched_state.set_status("error", str(e))
            if consecutive_failures >= max_consecutive_failures:
                print(f"{RED}Maximum consecutive failures reached. Exiting...{RESET}")
                sys.exit(1)

        if sched_state is not None:
            sched_state.set_last_run(datetime.now())

        # Re-scan trailer files to pick up newly downloaded trailers
        if _tracker:
            _scan_trailers(_tracker)
        # Refresh the library cache for the web UI
        try:
            from webui.routes import refresh_library_cache
            refresh_library_cache()
        except Exception:
            pass

def _scan_trailers(tracker):
    """Scan Plex media directories for trailer files and update the tracker."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception:
        print(f"{ORANGE}Skipping trailer scan — could not read config.{RESET}")
        return

    plex_url = config.get("PLEX_URL", "")
    plex_token = config.get("PLEX_TOKEN", "")
    if not plex_url or not plex_token or plex_token == "YOUR_PLEX_TOKEN":
        print(f"{ORANGE}Skipping trailer scan — Plex credentials not configured.{RESET}")
        return

    # Clean up entries for deleted files first
    removed = tracker.remove_missing()
    if removed:
        print(f"Cleaned up {removed} missing trailer entries")

    print("Scanning media directories for trailer files...")
    try:
        from plexapi.server import PlexServer as _PS
        plex = _PS(plex_url, plex_token)
        dirs = []
        for lib_list_key in ["MOVIE_LIBRARIES", "TV_LIBRARIES"]:
            for lib in config.get(lib_list_key, []):
                lib_name = lib.get("name", "") if isinstance(lib, dict) else lib
                try:
                    section = plex.library.section(lib_name)
                    dirs.extend(section.locations)
                except Exception:
                    pass
        if dirs:
            found = tracker.scan_directories(dirs)
            if found:
                print(f"Indexed {found} new trailer files (total: {tracker.count()})")
            else:
                print(f"Trailer index up to date ({tracker.count()} files tracked)")
    except Exception as e:
        print(f"{ORANGE}Could not scan for existing trailers: {e}{RESET}")


def _init_webui_and_tracker(sched_state=None):
    """Initialize the trailer tracker and web UI."""
    from Modules.trailer_tracker import TrailerTracker

    tracker = TrailerTracker()

    # Start web UI first so it's available immediately
    try:
        from webui import start_webui
        start_webui(
            scheduler_state=sched_state,
            config_path=config_path,
            trailer_tracker=tracker,
            version=VERSION,
        )
    except ImportError:
        print(f"{ORANGE}Web UI dependencies not available (install flask){RESET}")
    except Exception as e:
        print(f"{ORANGE}Web UI not started: {e}{RESET}")

    # Scan media directories to index existing trailers (after webUI is up)
    _scan_trailers(tracker)

    return tracker


def main():
    global _tracker
    if IS_DOCKER:
        # In Docker, run continuously on a schedule with web UI
        from Modules.scheduler_state import SchedulerState
        config_dir = os.path.dirname(config_path)
        sched_state = SchedulerState(config_dir=config_dir)
        _tracker = _init_webui_and_tracker(sched_state)
        run_scheduled(sched_state)
    else:
        # Outside Docker, still start web UI but run once
        _tracker = _init_webui_and_tracker()
        run_once()
        # Re-scan to pick up newly downloaded trailers
        if _tracker:
            _scan_trailers(_tracker)


if __name__ == "__main__":
    main()
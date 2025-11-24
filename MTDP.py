import os
import subprocess
import sys
import yaml
import requests
from plexapi.server import PlexServer
from datetime import datetime
import time
import signal

VERSION= "2025.11.2401"

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
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        print(f"Connection to Plex: {GREEN}Successful{RESET}")
        return plex
    except Exception:
        sys.exit(f"Connection to Plex: {RED}Failed - Please verify your Plex URL and Token in config.yml{RESET}")


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

    if choice == "1":
        print("\nLaunching Movies script...")
        subprocess.run([sys.executable, movies_script_path])
    elif choice == "2":
        print("\nLaunching TV Shows script...")
        subprocess.run([sys.executable, tv_shows_script_path])
    elif choice == "3":
        print("\nLaunching Movies script...")
        subprocess.run([sys.executable, movies_script_path])
        print("\nLaunching TV Shows script...")
        subprocess.run([sys.executable, tv_shows_script_path])

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


def run_scheduled():
    """Run the script on a schedule in Docker"""
    print(f"{GREEN}Starting Missing Trailer Downloader for Plex in scheduled mode{RESET}")
    
    # Get schedule from environment or config
    schedule_hours = int(os.environ.get('SCHEDULE_HOURS', '24'))
    
    print(f"Will run every {schedule_hours} hours")
    
    while True:
        try:
            print(f"\n{GREEN}Starting scheduled run at {datetime.now()}{RESET}")
            run_once()
            print(f"{GREEN}Scheduled run completed. Next run in {schedule_hours} hours{RESET}")
            
            # Sleep for the specified interval
            time.sleep(schedule_hours * 3600)
            
        except KeyboardInterrupt:
            print(f"\n{ORANGE}Received interrupt signal. Exiting...{RESET}")
            break
        except Exception as e:
            print(f"{RED}Error during scheduled run: {e}{RESET}")
            print(f"Will retry in {schedule_hours} hours")
            time.sleep(schedule_hours * 3600)


def main():
    if IS_DOCKER:
        # In Docker, run continuously on a schedule
        run_scheduled()
    else:
        # Outside Docker, run once
        run_once()


if __name__ == "__main__":
    main()
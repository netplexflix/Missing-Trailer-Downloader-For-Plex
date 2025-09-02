import os
import subprocess
import sys
import yaml
import requests
from plexapi.server import PlexServer
from datetime import datetime

# Get the directory of the script being executed
script_dir = os.path.dirname(os.path.abspath(__file__))

# Resolve paths for requirements, config, and module scripts
requirements_path = os.path.join(script_dir, "requirements.txt")
_container_mode = script_dir == "/app"
if _container_mode:
    config_path = os.path.join("/app/config", "config.yml")
else:
    config_path = os.path.join(script_dir, "config.yml")

movies_script_path = os.path.join(script_dir, "Modules", "Movies.py")
tv_shows_script_path = os.path.join(script_dir, "Modules", "TV Shows.py")

# ANSI color codes
GREEN = '\033[32m'
ORANGE = '\033[33m'
RED = '\033[31m'
RESET = '\033[0m'


# Version check
def check_version():
    try:
        response = requests.get("https://github.com/netplexflix/Missing-Trailer-Downloader-For-Plex/releases/latest")
        if response.status_code == 200:
            latest_version = response.url.split('/')[-1]
            current_version = "1.8"
            if latest_version > current_version:
                print(f"{ORANGE}A newer version ({latest_version}) is available!{RESET}")
            else:
                print(f"You are using the latest version.")
        else:
            print(f"{RED}Failed to check for updates.{RESET}")
    except Exception as e:
        print(f"{RED}Error checking version: {e}{RESET}")


# Check requirements
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
                # Handle both >= and == version specifiers
                if ">=" in req:
                    pkg_name, required_version = req.split(">=")
                    comparison_operator = ">="
                elif "==" in req:
                    pkg_name, required_version = req.split("==")
                    comparison_operator = "=="
                else:
                    # For packages without version specification
                    pkg_name = req
                    required_version = None
                    comparison_operator = None
                
                pkg_name = pkg_name.strip()
                if required_version:
                    required_version = required_version.strip()

                # Use text=True and errors="replace" for pip show output
                installed_info = subprocess.check_output(
                    [sys.executable, "-m", "pip", "show", pkg_name],
                    text=True,
                    errors="replace"
                )
                installed_version = installed_info.split("Version: ")[1].split("\n")[0]

                if not required_version:
                    print(f"{pkg_name}: {GREEN}OK{RESET}")
                elif comparison_operator == ">=":
                    if installed_version >= required_version:
                        print(f"{pkg_name}: {GREEN}OK{RESET}")
                    else:
                        print(f"{pkg_name}: {ORANGE}Upgrade needed{RESET}")
                        unmet_requirements.append(req)
                elif comparison_operator == "==":
                    if installed_version == required_version:
                        print(f"{pkg_name}: {GREEN}OK{RESET}")
                    else:
                        print(f"{pkg_name}: {ORANGE}Version mismatch{RESET}")
                        unmet_requirements.append(req)
                
            except (IndexError, subprocess.CalledProcessError) as e:
                print(f"{pkg_name if 'pkg_name' in locals() else req}: {RED}Missing or error: {str(e)}{RESET}")
                unmet_requirements.append(req)

        if unmet_requirements:
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
    MOVIE_LIBRARY_NAME = config.get("MOVIE_LIBRARY_NAME")
    TV_LIBRARY_NAME = config.get("TV_LIBRARY_NAME")

    try:
        plex.library.section(MOVIE_LIBRARY_NAME)
        print(f"Movie Library ({MOVIE_LIBRARY_NAME}): {GREEN}OK{RESET}")
    except Exception:
        errors.append(f"Movie Library ({MOVIE_LIBRARY_NAME}): {RED}Not Found - Please verify library name in config.yml{RESET}")

    try:
        plex.library.section(TV_LIBRARY_NAME)
        print(f"TV Library ({TV_LIBRARY_NAME}): {GREEN}OK{RESET}")
    except Exception:
        errors.append(f"TV Library ({TV_LIBRARY_NAME}): {RED}Not Found - Please verify library name in config.yml{RESET}")

    if errors:
        for error in errors:
            print(error)
        sys.exit(f"{RED}Library check failed.{RESET}")


# Launch scripts based on LAUNCH_METHOD
def launch_scripts(config):
    MOVIE_LIBRARY_NAME = config.get("MOVIE_LIBRARY_NAME")
    TV_LIBRARY_NAME = config.get("TV_LIBRARY_NAME")
    LAUNCH_METHOD = config.get("LAUNCH_METHOD", "0")
    start_time = datetime.now()

    if LAUNCH_METHOD == "0":
        print("\nChoose an option:")
        print(f"1 = {MOVIE_LIBRARY_NAME}")
        print(f"2 = {TV_LIBRARY_NAME}")
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


def main():
    # Print title
    print(f"Missing Trailer Downloader for Plex 1.7")

    # Always check for latest version
    check_version()

    # Load config before deciding on requirements check
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except Exception as e:
        sys.exit(f"{RED}Failed to load config.yml: {e}{RESET}")

    # Only check requirements if LAUNCH_METHOD is "0"
    LAUNCH_METHOD = config.get("LAUNCH_METHOD", "0")
    if LAUNCH_METHOD == "0":
        check_requirements()

    # Proceed with the rest of your flow
    plex = check_plex_connection(config)
    check_libraries(config, plex)
    launch_scripts(config)


if __name__ == "__main__":
    main()

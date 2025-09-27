# 📺 Missing Trailer Downloader for Plex 🎬

I initially bought a [Plex Pass](https://www.plex.tv/plex-pass/) because I wanted to have Trailers for my movies and TV Shows.<br/>
For a minority of titles, Plex doesn't seem to have trailers however. Especially lesser known and foreign titles.

This script will fill those gaps.

## 🚀 Quick Start (Docker - Recommended)

**For beginners, this is the easiest way to get started:**

```bash
# 1. Clone the repository
git clone https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex.git
cd Missing-Trailer-Downloader-for-Plex

# 2. Run the automated setup
chmod +x setup.sh
./setup.sh

# 3. Edit your Plex configuration
nano config/config.yml

# 4. Restart the container
docker-compose restart
```

**That's it!** Your container will now automatically check for missing trailers every hour.

> [!TIP] > **New to Docker?** No problem! The setup script handles everything for you. Just make sure Docker is installed and running.

---

## ✨ Features

- 🔍 **Detects Missing Trailers**: Scans your Plex libraries for items that lack trailers. (either Plex Pass or local)
- ▼ **Filters out specified Genres**: You may not want trailers for concerts or 3 minute shorts..
- ℹ️ **Informs**: Lists trailers missing, downloaded, failed, skipped, or if none are missing.
- 🎥 **Automatic Downloading**: Uses [YT-DLP](https://github.com/yt-dlp/yt-dlp) with Deno support to fetch the best available trailer from Youtube.
- 📂 **Organized Storage**: Trailers are saved according to Plex guidelines for both Movies and TV Shows.
- 🔄 **Refreshes Metadata**: Refreshes metadata of items with new trailer. (Necessary for Plex to 'detect' them)
- 📝 **Logging**: Keeps a log of your runs for each library.
- 🐳 **Docker Ready**: One-command setup with all dependencies included
- ⏰ **Automated Scheduling**: Runs continuously and checks every hour (Docker mode)
- 🔧 **Easy Configuration**: Simple setup with automated configuration file creation

---

## 🛠️ Installation

### 1️⃣ Download the script

Clone the repository:

```sh
git clone https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex.git
cd Missing-Trailer-Downloader-for-Plex
```

![#c5f015](https://placehold.co/15x15/c5f015/c5f015.png) Or simply download by pressing the green 'Code' button above and then 'Download Zip'.

### 2️⃣ Install Dependencies

- Ensure you have [Python](https://www.python.org/downloads/) installed (`>=3.8` recommended). <br/>
- Open a Terminal in the script's directory
  > [!TIP]
  > Windows Users: <br/>
  > Go to the script folder (where MTDP.py is). Right mouse click on an empty space in the folder and click `Open in Windows Terminal`
- Install the required dependencies by pasting the following code:

```sh
pip install -r requirements.txt
```

### 3️⃣ Install ffmpeg

[ffmpeg ](https://www.ffmpeg.org/) is required by yt-dlp to do postprocessing.
Check [THIS WIKI](https://www.reddit.com/r/youtubedl/wiki/ffmpeg/#wiki_where_do_i_get_ffmpeg.3F) for more information on how to install ffmpeg.

---

## 🐋 Installation via Docker (Recommended)

This script can be run in a Docker container, which will run continuously and check your Plex libraries automatically. The Docker setup includes all dependencies including the latest yt-dlp with Deno support.

### 🚀 Quick Start (Easiest Method)

1. **Clone the repository:**

   ```bash
   git clone https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex.git
   cd Missing-Trailer-Downloader-for-Plex
   ```

2. **Run the automated setup:**

   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

3. **Configure your Plex settings:**

   ```bash
   nano config/config.yml
   ```

4. **Restart the container:**
   ```bash
   docker-compose restart
   ```

That's it! Your container is now running and will check for missing trailers every hour.

### 📋 Manual Docker Setup

If you prefer to set up manually:

1. **Clone and prepare:**

   ```bash
   git clone https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex.git
   cd Missing-Trailer-Downloader-for-Plex
   mkdir -p config logs
   cp config.yml config/config.yml
   ```

2. **Start the container:**

   ```bash
   docker-compose up -d
   ```

3. **Edit configuration:**

   ```bash
   nano config/config.yml
   ```

4. **Restart after configuration:**
   ```bash
   docker-compose restart
   ```

### 🔧 Docker Commands

```bash
# View logs
docker-compose logs -f

# Stop the container
docker-compose down

# Restart the container
docker-compose restart

# Access container shell for debugging
docker-compose exec missing-trailer-downloader /bin/bash

# Rebuild and start (after code changes)
docker-compose up -d --build
```

### 🐳 What's Included in Docker

- ✅ **Python 3.12** with all dependencies
- ✅ **yt-dlp with Deno** - Latest version with JavaScript runtime support
- ✅ **FFmpeg** - For video processing
- ✅ **Automatic scheduling** - Runs every hour
- ✅ **Logging** - Comprehensive logs in `./logs` directory
- ✅ **Multi-architecture** - Supports AMD64 and ARM64

### 🔍 Troubleshooting Docker

**Check if everything is working:**

```bash
# Test Deno installation
docker-compose exec missing-trailer-downloader deno --version

# Test yt-dlp installation
docker-compose exec missing-trailer-downloader yt-dlp --version

# View application logs
docker-compose logs -f missing-trailer-downloader
```

**Common issues:**

- Make sure Docker is running before starting
- Ensure your Plex server is accessible from the container
- Check that your Plex token is valid and has the right permissions

### 🏠 unRAID Users

For unRAID users, you can run this container using the Community Applications or manually:

#### Option 1: Community Applications (Recommended)

1. Go to **Apps** → **Community Applications**
2. Search for "Missing Trailer Downloader"
3. Install and configure with your Plex details

#### Option 2: Manual Docker Setup

1. Go to **Docker** → **Add Container**
2. Use these settings:

   - **Repository**: `netplexflix/missing-trailer-downloader:latest`
   - **Name**: `missing-trailer-downloader`
   - **Network Type**: Bridge
   - **Restart Policy**: Unless Stopped

3. **Environment Variables**:

   - `TZ` = Your timezone (e.g., `America/New_York`)
   - `PUID` = Your user ID (usually `1000`)
   - `PGID` = Your group ID (usually `1000`)

4. **Volume Mappings**:

   - **Host Path**: `/mnt/user/appdata/missing-trailer-downloader`
   - **Container Path**: `/config`
   - **Access Mode**: Read/Write

5. **Port Mappings**: None required

6. **Post-Arguments**: Leave empty

#### Option 3: Docker Compose (Advanced)

```bash
# SSH into your unRAID server
cd /mnt/user/appdata/missing-trailer-downloader
wget https://raw.githubusercontent.com/netplexflix/Missing-Trailer-Downloader-for-Plex/main/docker-compose.hub.yml
mv docker-compose.hub.yml docker-compose.yml
docker-compose up -d
```

#### unRAID Configuration

1. After starting the container, edit the config file:

   ```bash
   nano /mnt/user/appdata/missing-trailer-downloader/config/config.yml
   ```

2. Set your Plex details:

   - `PLEX_URL`: Your Plex server URL (e.g., `http://192.168.1.100:32400`)
   - `PLEX_TOKEN`: Your Plex token
   - `MOVIE_LIBRARY_NAME`: Your movie library name
   - `TV_LIBRARY_NAME`: Your TV library name

3. Restart the container to apply changes

#### unRAID Troubleshooting

- **Check logs**: Go to Docker → missing-trailer-downloader → Logs
- **File permissions**: Ensure the appdata folder has proper permissions
- **Plex connectivity**: Make sure your Plex server is accessible from the container

## ⚙️ Configuration

### 📝 Docker Users

Edit the `config/config.yml` file (created automatically during setup) with your Plex details.

### 🖥️ Local Installation Users

Edit the `config.yml` file in the project root directory.

### 🔧 Configuration Options

- **LAUNCH_METHOD:** 0 = Choose at runtime, 1 = Movies only, 2 = TV Shows only, 3 = Both
- **PLEX_URL:** Your Plex server URL (e.g., `http://192.168.1.100:32400`)
- **PLEX_TOKEN:** [How to find your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- **MOVIE_LIBRARY_NAME:** The exact name of your Movie library in Plex
- **TV_LIBRARY_NAME:** The exact name of your TV Show library in Plex
- **TV_GENRES_TO_SKIP:** Add or remove any genres to be skipped when checking your TV Shows
- **MOVIE_GENRES_TO_SKIP:** Add or remove any genres to be skipped when checking your Movies
- **CHECK_PLEX_PASS_TRAILERS:** Default: `true` will check for Plex Pass Trailers. If set to `false` it will download all trailers locally.
- **DOWNLOAD_TRAILERS:** `true` will download the missing trailers. `false` will simply list them.
- **PREFERRED_LANGUAGE:** Default: `original`. When set to another language (eg: `french` or `german`), yt-dlp will attempt to download a trailer in that language
- **SHOW_YT_DLP_PROGRESS:** Can be set to `true` for debugging.
- **SKIP_CHANNELS:** Add YouTube channel names that create fake or bad quality trailers so they will be skipped.

### 🐳 Docker Environment Variables

You can also customize the Docker container using environment variables in `docker-compose.yml`:

- **TZ:** Timezone (default: `America/New_York`)
- **PUID:** User ID for file permissions (default: `1000`)
- **PGID:** Group ID for file permissions (default: `1000`)

---

## 🚀 Usage

### 🐳 Docker Users (Recommended)

The Docker container runs automatically and checks for missing trailers every hour. No manual intervention needed!

**To check status:**

```bash
# View logs
docker-compose logs -f

# Check if container is running
docker-compose ps
```

**To run manually (one-time scan):**

```bash
# Access container and run manually
docker-compose exec missing-trailer-downloader python MTDfP.py
```

### 🖥️ Local Installation Users

Open a Terminal in your script directory and launch the script with:

```sh
python MTDP.py
```

You'll be prompted to choose:

- **1**: Run Movie library.
- **2**: Run TV shows library.
- **3**: Scan both consecutively.

Alternatively, pre-set your preferred method in `config.yml` (`LAUNCH_METHOD` field) to bypass selection.

> [!TIP]
> Windows users can create a batch file to quickly launch the script.<br/>
> Type `"[path to your python.exe]" "[path to the script]" -r pause"` into a text editor
>
> For example:
>
> ```
> "C:\Users\User1\AppData\Local\Programs\Python\Python311\python.exe" "P:\Scripts\Missing Trailer Downloader for Plex\MTDP.py" -r
> pause
> ```
>
> Save as a .bat file. You can now double click this batch file to directly launch the script.<br/>
> You can also use this batch file to [schedule](https://www.windowscentral.com/how-create-automated-task-using-task-scheduler-windows-10) the script to run daily/weekly/etc.

---

### ⚠️ **Do you Need Help or have Feedback?**

- Join the [Discord](https://discord.gg/VBNUJd7tx3).
- Open an [Issue](https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex/issues) on GitHub.

---

## 🤝 Trailarr

Check out [Trailarr](https://github.com/nandyalu/trailarr) if you want to ignore Plex Pass Trailers and want a UI, running in Docker!</br>
Requires Radarr and Sonarr.

<a href="https://github.com/nandyalu/trailarr">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/nandyalu/trailarr/main/assets/images/trailarr-full-512-lg.png"
    >
    <source
      media="(prefers-color-scheme: light)"
      srcset="https://raw.githubusercontent.com/nandyalu/trailarr/main/assets/images/trailarr-full-light-512-lg.png"
    >
    <img
      alt="Trailarr logo with name"
      src="https://raw.githubusercontent.com/nandyalu/trailarr/main/assets/images/trailarr-full-primary-512-lg.png"
      width="20%"
    >
  </picture>
</a>

---

### ❤️ Support the Project

If you like this project, please ⭐ star the repository and share it with the community!

<br/>

[!["Buy Me A Coffee"](https://github.com/user-attachments/assets/5c30b977-2d31-4266-830e-b8c993996ce7)](https://www.buymeacoffee.com/neekokeen)

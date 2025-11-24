# 📺 Missing Trailer Downloader for Plex 🎬

I initially bought a [Plex Pass](https://www.plex.tv/plex-pass/) because I wanted to have Trailers for my movies and TV Shows.<br/>
For a minority of titles, Plex doesn't seem to have trailers however. Especially lesser known and foreign titles.

This script will fill those gaps.

---

## ✨ Features
- 🔍 **Detects Missing Trailers**: Scans your Plex libraries for items that lack trailers. (either Plex Pass or local)
-  ▼ **Filters out specified Genres**: You may not want trailers for concerts or 3 minute shorts..
- ℹ️ **Informs**: Lists trailers missing, downloaded, failed, skipped, or if none are missing.
- 🎥 **Automatic Downloading**: Uses [YT-DLP](https://github.com/yt-dlp/yt-dlp) to fetch the best available trailer from Youtube.
- 📂 **Organized Storage**: Trailers are saved according to Plex guidelines for both Movies and TV Shows. 
- 🔄 **Refreshes Metadata**: Refreshes metadata of items with new trailer. (Necessary for Plex to 'detect' them)
- 📝 **Logging**: Keeps a log of your runs for each library.

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
>[!TIP]
>Windows Users: <br/>
>Go to the script folder (where MTDfP.py is). Right mouse click on an empty space in the folder and click `Open in Windows Terminal`
- Install the required dependencies by pasting the following code:
```sh
pip install -r requirements.txt
```

### 3️⃣ Install ffmpeg
[ffmpeg ](https://www.ffmpeg.org/) is required by yt-dlp to do postprocessing.
Check [THIS WIKI](https://www.reddit.com/r/youtubedl/wiki/ffmpeg/#wiki_where_do_i_get_ffmpeg.3F) for more information on how to install ffmpeg.

---

## 🐋 Installation via Docker

#### Step 1: Install Docker
1. **Download Docker Desktop** from [docker.com](https://www.docker.com/products/docker-desktop/)
2. **Install and start Docker Desktop** on your computer
3. **Verify installation**: Open a terminal/command prompt and type `docker --version` - you should see a version number

#### Step 2: Create Docker Compose File

1. **Create a new folder** for MTDP on your computer (e.g., `C:\MTDP` or `/home/user/MTDP`)
2. **Create a new file** called `docker-compose.yml` in that folder
3. **Copy and paste this content** into the file:

```yaml
version: '3.8'

services:
  mtdp:
    image: netplexflix/mtdp:latest
    container_name: mtdp
    environment:
      - PUID=1000  # Change to your user ID
      - PGID=1000  # Change to your group ID
      - TZ=America/New_York  # Change to your timezone
      - SCHEDULE_HOURS=24  # Run every X hours (default: 24)
    volumes:
      - ./config:/config  # Mount your config directory
      - ./logs:/app/Logs  # Optional: persist logs
      - /path/to/media:/media  # Mount your media directory (same as Plex sees it)
    restart: unless-stopped
```

4. **Update the timezone** in the `TZ` environment variable to [match your location](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g.: `America/New_York`, `Europe/London`, `Asia/Tokyo`)
5. **Update PUID/PGID** to match your system user (optional - defaults to 1000:1000)

#### Step 3: Update Media Paths
- You must update the media paths in the existing `docker-compose.yml` file.
- The media path in the container (/media) must match how Plex sees the files
- Example:
On thehost system your Movies are at /mnt/storage/movies
Plex sees them at: /media/movies
The mount:
```
volumes:
  - /mnt/storage:/media
```

### Step 4: Create your config 
- create a `config` directory and download the example config to it
- Configure your settings. See [⚙️ Configuration](#️-configuration)


#### Step 5: Pull the image
1. **Open a terminal/command prompt** in your MTDP folder
2. **Type this command** and press Enter:
   ```bash
   docker-compose pull
   ```

#### Step 6: Run MTDP
1. **Type this command** and press Enter:
   ```bash
   docker-compose up -d
   ```


## ⚙️ Configuration
Edit the `config.yml` file to set your Plex details and desired variables:

### 📋 Basic Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `LAUNCH_METHOD` | `'0'`, `'1'`, `'2'`, `'3'` | **0** = Choose at runtime, **1** = Movies only, **2** = TV Shows only, **3** = Both consecutively |
| `PLEX_URL` | `'http://localhost:32400'` | URL of your Plex server (change if needed) |
| `PLEX_TOKEN` | `'YOUR_PLEX_TOKEN'` | Authentication token for Plex API access ([How to find your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)) |
| `USE_LABELS` | `true`, `false` | Whether to use MTDfP labels to track processed items |

### 🎬 Trailer Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `CHECK_PLEX_PASS_TRAILERS` | `true`, `false` | Check for existing Plex Pass trailers before downloading (default: `true`) |
| `DOWNLOAD_TRAILERS` | `true`, `false` | Whether to actually download missing trailers (`false` will only list them) |
| `PREFERRED_LANGUAGE` | `'original'`, `'english'`, `'german'`, `'french'`, etc. | Language preference for trailer downloads (default: `'original'`). You can also use multiple terms like `'german deutsch'` |
| `REFRESH_METADATA` | `true`, `false` | Refresh Plex metadata after downloading trailers |

### 🔧 Advanced Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `SHOW_YT_DLP_PROGRESS` | `true`, `false` | Show detailed yt-dlp download progress (useful for debugging) |
| `YT_DLP_CUSTOM_OPTIONS` | `"your custom command"` | Any custom options/commands you'd like to pass to yt-dlp |

### 📚 Library Configuration
The script supports multiple libraries for both Movies and TV Shows. You can configure multiple libraries with individual genre skip lists.

#### Multiple Libraries with Genre Filtering
```yaml
# TV Libraries Configuration
# Configure multiple TV show libraries with individual genre skip lists
# Each library can have different genres_to_skip settings
TV_LIBRARIES:
  - name: 'TV Shows'
    genres_to_skip:
      - 'Talk Show'
      - 'Stand-Up'
      - 'News'
  - name: 'Anime TV'
    genres_to_skip:
      - 'Talk Show'
      - 'Stand-Up'
      - 'News'
      - 'Reality'
  - name: 'Documentaries'
    genres_to_skip:
      - 'Talk Show'
      - 'Stand-Up'

# Movie Libraries Configuration
# Configure multiple movie libraries with individual genre skip lists
# Each library can have different genres_to_skip settings
MOVIE_LIBRARIES:
  - name: 'Movies'
    genres_to_skip:
      - 'Short'
      - 'Stand-Up'
      - 'Concert'
  - name: 'Anime Movies'
    genres_to_skip:
      - 'Short'
      - 'Stand-Up'
      - 'Concert'
  - name: 'Documentaries'
    genres_to_skip:
      - 'Short'
      - 'Stand-Up'
```

#### Multiple Libraries without Genre Filtering
```yaml
# TV Libraries Configuration
# Configure multiple TV show libraries
# Each library will process all genres (no filtering)
TV_LIBRARIES:
  - name: 'TV Shows'
  - name: 'Kids TV Shows'

# Movie Libraries Configuration
# Configure multiple movie libraries
# Each library will process all genres (no filtering)
MOVIE_LIBRARIES:
  - name: 'Movies'
  - name: 'Kids Movies'
```

---

## 🍪 Using browser cookies for yt-dlp

In case you need to use your browser's cookies, you can pass them along to yt-dlp.<br>
To extract your cookies in Netscape format, you can use an extension:
  * [Firefox](https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/)
  * [Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)<br>
Extract the cookies you need and rename the file `cookies.txt`

#### For Docker Users:

Add the path to the folder containing your `cookies.txt` to your docker-compose.yml under `volumes:`:

```yaml
      - /path/to/cookies:/cookies
```

#### For Local Installation

1. Create a `cookies` folder in the same directory as the MTDP script
2. Export your browser cookies to `cookies.txt` within this new subfolder

---

## 🚀 Usage - Running the Script

Open a Terminal in your script directory and launch the script with:
```sh
python MTDfP.py
```
You’ll be prompted to choose:
- **1**: Run Movie library.
- **2**: Run TV shows library.
- **3**: Scan both consecutively.

Alternatively, pre-set your preferred method in `config.yml` (`LAUNCH_METHOD` field) to bypass selection.

> [!TIP]
> Windows users can create a batch file to quickly launch the script.<br/>
> Type `"[path to your python.exe]" "[path to the script]" -r pause"` into a text editor
>
> For example:
> ```
>"C:\Users\User1\AppData\Local\Programs\Python\Python311\python.exe" "P:\Scripts\Missing Trailer Downloader for Plex\MTDfP.py" -r
>pause
> ```
> Save as a .bat file. You can now double click this batch file to directly launch the script.<br/>
> You can also use this batch file to [schedule](https://www.windowscentral.com/how-create-automated-task-using-task-scheduler-windows-10) the script to run daily/weekly/etc.

---

## 🤝  <img width="113" height="26" alt="Image" src="https://github.com/user-attachments/assets/e70c305a-c504-4ed1-bfdd-b1cf52ef6a19" />
Check out [Trailarr](https://github.com/nandyalu/trailarr) if you want to ignore Plex Pass Trailers and want a UI, running in Docker!</br>

| Main Differences: | Trailarr | MTDfP |
| :--- | :---: | ---: |
| GUI | ✅ | ❌ |
| unRAID Template | ✅ | ❌ |
| Requires Radarr and Sonarr | ✅ | ❌ |
| Requires Plex | ❌ | ✅ |
| Automatically refreshes Plex metadata (required for Plex to detect the trailers) | ❌ | ✅ |
| Can skip download if trailer is already available via Plex Pass | ❌ | ✅ |

---  
### ❤️ Support the Project
If you like this project, please ⭐ star the repository and share it with the community!

<br/>

[!["Buy Me A Coffee"](https://github.com/user-attachments/assets/5c30b977-2d31-4266-830e-b8c993996ce7)](https://www.buymeacoffee.com/neekokeen)

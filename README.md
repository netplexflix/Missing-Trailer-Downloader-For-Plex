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

>[!TIP]
>User Healzangels created a Docker image on Dockerhub [HERE](https://hub.docker.com/r/healzangels/mtdfp).

This script can also be run in a Docker container, which will run continuously and check your Plex libraries once an hour.

Make sure you update the `config.yml` file with your Plex details and desired variables before running the container.

### 1️⃣ Clone the repository
Clone the repository:
```sh
git clone git clone https://github.com/netplexflix/Missing-Trailer-Downloader-for-Plex.git
cd Missing-Trailer-Downloader-for-Plex
```
![#c5f015](https://placehold.co/15x15/c5f015/c5f015.png) Or simply download by pressing the green 'Code' button above and then 'Download Zip'.

### 2️⃣ Build Image
Ensure you have [Docker](https://docs.docker.com/get-docker/) installed. Then, build the Docker image:
```sh
docker build -t mtdp .
```

### 3️⃣ Run the Container
Run the Docker container:
```sh
docker run -d -v /path/to/your/config:/app/config mtdp
```
Replace `/path/to/your/config` with the path to your `config.yml` file.

## ⚙️ Configuration
Edit the `config.yml` file to set your Plex details and desired variables:

### Basic Settings
- **LAUNCH_METHOD:** 0 = Choose at runtime, 1 = Movies only, 2 = TV Shows only, 3 = Both
- **PLEX_URL:** Change if needed.
- **PLEX_TOKEN:** [How to find your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

### Library Configuration
The script now supports multiple libraries for both Movies and TV Shows. You can configure multiple libraries with individual genre skip lists.

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

### Other Settings
- **CHECK_PLEX_PASS_TRAILERS:** Default: `true` will check for Plex Pass Trailers. If set to `false` it will download all trailers locally.
- **DOWNLOAD_TRAILERS:** `true` will download the missing trailers. `false` will simply list them.
- **PREFERRED_LANGUAGE:** Default: `original`. When set to another language (eg: `french` or `german`), yt-dlp will attempt to download a trailer in that language
- **SHOW_YT_DLP_PROGRESS:** Can be set to `true` for debugging.
- **MAP_PATH:** Default `false`. Set to `true` if you need PATH_MAPPINGS in case of NAS storage for example.
- **PATH_MAPPINGS:** Used to map paths: eg: If Plex looks for your movies in "/media/movies" and this directory is mapped on your computer as "P:/media/movies" you can map as followed: "/media": "P:/media"

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

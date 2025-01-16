# 📺 Missing Trailer Downloader for Plex 🎬

I initially bought a [Plex Pass](https://www.plex.tv/plex-pass/) because I wanted to have Trailers for my movies and TV Shows.<br/>
For a minority of titles, Plex doesn't seem to have trailers however. Especially lesser known and foreign titles.

This script will fill those gaps.

---

## ✨ Features
- 🔍 **Detects and lists Missing Trailers**: Scans your Plex Movie and/or TV show libraries for items that lack trailers. (either Plex Pass or local)
-  ▼ **Filters out specified Genres**: You may not want trailers for concerts or 3 minute shorts..
- 🎥 **Automatic Downloading**: Uses [YT-DLP](https://github.com/yt-dlp/yt-dlp) to fetch the best available trailer from Youtube.
- 📂 **Organized Storage**: Trailers are saved according to Plex guidelines for both Movies and TV Shows. 
- 🔄 **Library Refreshing**: Refreshes Plex metadata of items for which a trailer was downloaded. (Necessary for Plex to 'detect' them)
- 📝 **Logging**: Keeps a log of your runs for each library.

---

## 🛠️ Installation

### 1️⃣ Download the script
Clone the repository:
```sh
git clone https://github.com/yourusername/Missing-Trailer-Downloader-for-Plex.git
cd Missing-Trailer-Downloader-for-Plex
```
Or simply download by pressing the green 'Code' button above and then 'Download Zip'.

### 2️⃣ Install Dependencies
Ensure you have [Python](https://www.python.org/downloads/) installed (`>=3.8` recommended). Then, install the required dependencies:
```sh
pip install -r requirements.txt
```
> [!TIP]  
> If you just launch the script it will check the required dependencies for you and prompt you to install or upgrade them if needed.

---

## ⚙️ Configuration
Edit the `config.yml` file to set your Plex details and desired variables:

- **LAUNCH_METHOD:** 0 = Choose at runtime, 1 = Movies only, 2 = TV Shows only, 3 = Both
- **PLEX_URL:** Change if needed.
- **PLEX_TOKEN:** [How to find your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
- **MOVIE_LIBRARY_NAME:** The name of your Movie library in Plex
- **TV_LIBRARY_NAME:** The name of your TV Show library in Plex
- **TV_GENRES_TO_SKIP:** Add or remove any genres to be skipped when checking your TV Shows
- **MOVIE_GENRES_TO_SKIP:** Add or remove any genres to be skipped when checking your Movies
- **DOWNLOAD_TRAILERS:** `true` will download the missing trailers. `false` will simply list them.
- **SHOW_YT_DLP_PROGRESS:** Can be set to `true` for debugging.

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
> Save as a .bat file. You can now double click this batch file to directly launch the script.
---


### ⚠️ **Do you Need Help or have Feedback?**
- Join the [Discord](https://discord.gg/sWQ5m2qM).
- Open an [Issue](https://github.com/yourusername/Missing-Trailer-Downloader-for-Plex/issues) on GitHub.


---

## 🎉 Contributing
Contributions are welcome! If you find a bug or want to improve the script:
1. Fork the repository 🍴
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -m "Added new feature"`
4. Push to the branch: `git push origin feature-name`
5. Open a Pull Request 🚀

---
## 🤝 Trailarr
Check out [Trailarr](https://github.com/nandyalu/trailarr) if you want to ignore Plex Pass Trailers and want to download Trailers for ALL your content.</br>
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

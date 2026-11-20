# Changelog

All notable changes to **Metadata Updatarr** (`script.metadata.updatarr`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-11-20

### Added
- **Unified Add-on (`script.metadata.updatarr`):** Combines full Video Versions management (from `script.versions.updatarr` v0.2.0) with Missing Metadata recovery.
- **Season Plots & Names Updater (`lib/metadata_updater.py`):**
  - Scans `seasons` for missing plots (`seasons.plot`) and empty/generic names (`seasons.name`).
  - Reads local `seasonXX.nfo` and `tvshow.nfo` (`<namedseason>`, `<season>`) first, with automatic fallback to **TMDbHelper** (`plugin.video.themoviedb.helper`) or an optional TMDB API key (`lib/tmdbhelper_client.py`).
- **Missing Movie Plots & Taglines:** Fills missing `movie.c01` (plot) and `movie.c03` (tagline) from local `.nfo` files with TMDbHelper fallback.
- **Missing Metadata Scope Picker & Performance:**
  - Added per-scope selection (**Seasons Only**, **Movies Only**, **TV Shows Only**, **Episodes Only**, **All Incremental**, **All Full Rescan**).
  - Added per-folder `list_dir` NFO caching, incremental `metadata_scan_state.json`, thread-safety locks in `MetadataUpdater` and `TMDbFallbackClient`, IMDB-to-TMDB `/find` fallback resolution, and preloaded link caches (`genre_link`, `studio_link`, `actor_link`).
- **Full Table Backup Coverage (`lib/backup_manager.py`):** MariaDB/MySQL backups snapshot and restore all touched metadata tables (`videoversion`, `videoversiontype`, `seasons`, `movie`, `tvshow`, `episode`, `streamdetails`).
- **Custom Artwork:** Generated distinct 512x512 `icon.png` and 1920x1080 `fanart.jpg` for **Metadata Updatarr**.
- **Combined Run & Service (`main.py`, `service.py`):** Added **Run All (Video Versions + Missing Metadata)** plus dedicated **Missing Metadata** scan & dry-run menu items and background service support.

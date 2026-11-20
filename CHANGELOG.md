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
- **Unified Backups (`lib/backup_manager.py`):** MariaDB/MySQL backups now also snapshot and restore `seasons_bak_<timestamp>` alongside `videoversion` and `videoversiontype`.
- **Combined Run & Service (`main.py`, `service.py`):** Added **Run All (Video Versions + Missing Metadata)** plus dedicated **Missing Metadata** scan & dry-run menu items and background service support.

# -*- coding: utf-8 -*-
"""
NFO parser for extracting video version and edition metadata.
"""
import os
import re
import html
import threading
import xml.etree.ElementTree as ET
from lib.kodi_compat import file_exists, read_text_file, list_dir, log
from lib.filename_parser import normalize_edition_name


def get_candidate_nfo_paths(video_path, video_filename):
    """
    Given a video folder path and filename, return candidate NFO file paths.
    Works with both local file paths and network VFS paths (nfs://, smb://).
    """
    base_folder = video_path.rstrip("/\\") + "/"
    name_without_ext = os.path.splitext(video_filename)[0]

    # Priority 1: Sidecar matching the exact video filename
    sidecar_nfo = base_folder + name_without_ext + ".nfo"
    # Priority 2: movie.nfo in the same directory
    movie_nfo = base_folder + "movie.nfo"

    return [sidecar_nfo, movie_nfo]


def parse_edition_from_xml(xml_content):
    """
    Parse XML string and search for version/edition tags.
    Returns cleaned edition string or None.
    """
    if not xml_content or not xml_content.strip():
        return None

    try:
        # Basic cleanup: remove invalid XML characters or XML declaration encoding mismatches
        root = ET.fromstring(xml_content)
    except Exception as e:
        # If XML parse failed, fallback to regex search
        log("XML parse error in NFO: {}, falling back to regex".format(e), "debug")
        return parse_edition_with_regex(xml_content)

    # 1. <videoassettitle> (Kodi native version tag)
    tag = root.find("videoassettitle")
    if tag is not None and tag.text and tag.text.strip():
        val = clean_version_string(tag.text)
        if val:
            return val

    # 2. <videoasset><title>
    asset = root.find("videoasset")
    if asset is not None:
        title_tag = asset.find("title")
        if title_tag is not None and title_tag.text and title_tag.text.strip():
            val = clean_version_string(title_tag.text)
            if val:
                return val

    # 3. <edition> (Radarr, Jellyfin, Emby, Plex)
    edition_tag = root.find("edition")
    if edition_tag is not None and edition_tag.text and edition_tag.text.strip():
        val = clean_version_string(edition_tag.text)
        if val:
            return val

    # 4. <version>
    version_tag = root.find("version")
    if version_tag is not None and version_tag.text and version_tag.text.strip():
        val = clean_version_string(version_tag.text)
        if val:
            return val

    return None


def parse_edition_with_regex(text):
    """Regex fallback to extract edition tags when XML parsing fails."""
    patterns = [
        r"<videoassettitle>\s*([^<]+?)\s*</videoassettitle>",
        r"<edition>\s*([^<]+?)\s*</edition>",
        r"<version>\s*([^<]+?)\s*</version>",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = clean_version_string(m.group(1))
            if val:
                return val
    return None


def clean_version_string(s):
    """Standardize and clean version text, resolving XML entities and enum names."""
    if not s:
        return None
    val = html.unescape(s.strip())
    # Normalize underscores/internal spaces
    val = re.sub(r"\s+", " ", val).strip()
    # Ignore blank or placeholder names
    if not val or val.lower() in ("default", "none", "unknown", "n/a", "na", "null"):
        return None
    return normalize_edition_name(val)


_NFO_LOCK = threading.Lock()
# Folder NFO cache: folder_path -> edition_str or None
_FOLDER_NFO_CACHE = {}
# Folder listing cache: folder_path -> dict(lower_name -> actual_name) or None
_FOLDER_FILES_CACHE = {}


def clear_nfo_cache():
    """Clear in-memory NFO and directory listing caches."""
    with _NFO_LOCK:
        _FOLDER_NFO_CACHE.clear()
        _FOLDER_FILES_CACHE.clear()


def _get_folder_nfo_files(base_folder):
    """Cache directory file listing once per folder to avoid repeated SMB stat calls."""
    with _NFO_LOCK:
        if base_folder in _FOLDER_FILES_CACHE:
            return _FOLDER_FILES_CACHE[base_folder]

    nfo_map = None
    try:
        _, files = list_dir(base_folder)
        if files is not None:
            nfo_map = {f.lower(): f for f in files if f.lower().endswith(".nfo")}
    except Exception:
        nfo_map = None

    with _NFO_LOCK:
        if base_folder not in _FOLDER_FILES_CACHE:
            _FOLDER_FILES_CACHE[base_folder] = nfo_map
        return _FOLDER_FILES_CACHE[base_folder]


def find_folder_edition_from_nfo(video_path):
    """Read and cache movie.nfo in a directory."""
    base_folder = video_path.rstrip("/\\") + "/"
    with _NFO_LOCK:
        if base_folder in _FOLDER_NFO_CACHE:
            return _FOLDER_NFO_CACHE[base_folder]

    nfo_map = _get_folder_nfo_files(base_folder)
    movie_nfo_name = None
    if nfo_map is not None:
        movie_nfo_name = nfo_map.get("movie.nfo")
        if not movie_nfo_name:
            with _NFO_LOCK:
                _FOLDER_NFO_CACHE[base_folder] = None
            return None

    movie_nfo = base_folder + (movie_nfo_name or "movie.nfo")
    edition = None
    try:
        if nfo_map is not None or file_exists(movie_nfo):
            content = read_text_file(movie_nfo)
            edition = parse_edition_from_xml(content)
            if edition:
                log("Found edition '{}' in directory NFO: {}".format(edition, movie_nfo), "debug")
    except Exception as e:
        log("Error reading directory NFO {}: {}".format(movie_nfo, e), "warning")
        edition = None

    with _NFO_LOCK:
        _FOLDER_NFO_CACHE[base_folder] = edition
    return edition


def find_edition_from_nfo(video_path, video_filename, allow_folder_nfo=True):
    """
    Search candidate NFO files for the movie/episode and return detected edition.
    Uses cached directory listings to minimize network VFS round-trips.
    """
    base_folder = video_path.rstrip("/\\") + "/"
    name_without_ext = os.path.splitext(video_filename)[0]
    target_sidecar_name = name_without_ext + ".nfo"

    nfo_map = _get_folder_nfo_files(base_folder)

    # Priority 1: Sidecar matching the exact video filename
    sidecar_actual = None
    if nfo_map is not None:
        sidecar_actual = nfo_map.get(target_sidecar_name.lower())
    if nfo_map is None or sidecar_actual:
        sidecar_nfo = base_folder + (sidecar_actual or target_sidecar_name)
        try:
            if sidecar_actual or file_exists(sidecar_nfo):
                content = read_text_file(sidecar_nfo)
                edition = parse_edition_from_xml(content)
                if edition:
                    log("Found edition '{}' in sidecar NFO: {}".format(edition, sidecar_nfo), "debug")
                    return edition
        except Exception as e:
            log("Error reading sidecar NFO {}: {}".format(sidecar_nfo, e), "warning")

    # Priority 2: movie.nfo in the directory (when allowed)
    if allow_folder_nfo:
        return find_folder_edition_from_nfo(video_path)

    return None

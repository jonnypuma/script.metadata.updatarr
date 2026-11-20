# -*- coding: utf-8 -*-
"""
Filename parser for detecting video editions and cuts from file/folder names.
"""
import os
import re
from lib.custom_mappings import apply_custom_mapping

# Explicit edition tag patterns (e.g. Radarr {edition-Director's Cut})
EXPLICIT_TAG_PATTERNS = [
    re.compile(r"[\{\[\(]edition[-_: ]+([^\}\]\)]+)[\}\]\)]", re.IGNORECASE),
]


def format_anniversary_edition(match):
    """Format anniversary numbers with proper English ordinals (1st, 2nd, 3rd, 50th)."""
    num_str = match.group(1)
    try:
        n = int(num_str)
        if 11 <= (n % 100) <= 13:
            sfx = "th"
        else:
            sfx = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return "{}{} Anniversary Edition".format(n, sfx)
    except ValueError:
        return "{}th Anniversary Edition".format(num_str)


# Standard edition keywords mapping (pattern -> normalized display name)
# Ordered by specificity (compound editions first)
KNOWN_EDITIONS = [
    # Compound editions
    (re.compile(r"\bunrated[\s._-]*director['’s]*[\s._-]*(?:cut|edition)\b", re.IGNORECASE), "Unrated Director's Cut"),
    (re.compile(r"\bextended[\s._-]*director['’s]*[\s._-]*(?:cut|edition)\b", re.IGNORECASE), "Extended Director's Cut"),
    (re.compile(r"\bultimate[\s._-]*collector['’s]*[\s._-]*(?:edition|cut)?\b", re.IGNORECASE), "Ultimate Collector's Edition"),
    (re.compile(r"\bcriterion[\s._-]*collection[\s._-]*(?:edition)?\b", re.IGNORECASE), "Criterion Collection Edition"),
    (re.compile(r"\bblack[\s._-]*(?:and|&)?[\s._-]*white[\s._-]*(?:edition|version)?\b", re.IGNORECASE), "Black and White Edition"),

    # Anniversary editions (e.g. 50th Anniversary Edition, 21st Anniversary)
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)?[\s._-]anniversary[\s._-]*(?:edition|cut|version)?\b", re.IGNORECASE),
     format_anniversary_edition),

    # Specific common cuts / editions
    (re.compile(r"\bdirector['’s]*[\s._-]*(?:cut|edition)\b", re.IGNORECASE), "Director's Cut"),
    (re.compile(r"\bextended[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "Extended Edition"),
    (re.compile(r"\btheatrical[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "Theatrical Cut"),
    (re.compile(r"\binternational[\s._-]*(?:cut|edition)\b", re.IGNORECASE), "International Cut"),
    (re.compile(r"\beuropean[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "European Cut"),
    (re.compile(r"\bbroadcast[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "Broadcast Cut"),
    (re.compile(r"\btelevision[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "Television Cut"),
    (re.compile(r"\btv[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "TV Cut"),
    (re.compile(r"\bus[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "US Cut"),
    (re.compile(r"\buk[\s._-]*(?:cut|edition|version)\b", re.IGNORECASE), "UK Cut"),
    (re.compile(r"\balternat(?:e|ive)[\s._-]*(?:cut|edition|ending|version)\b", re.IGNORECASE), "Alternate Cut"),
    (re.compile(r"\bunrated[\s._-]*(?:cut|edition|version)?\b", re.IGNORECASE), "Unrated Version"),
    (re.compile(r"\buncut[\s._-]*(?:version|edition)?\b", re.IGNORECASE), "Uncut Version"),
    (re.compile(r"\bremastered[\s._-]*(?:version|edition)?\b", re.IGNORECASE), "Remastered Version"),
    (re.compile(r"\bcollector['’s]*[\s._-]*(?:edition|cut)\b", re.IGNORECASE), "Collector's Edition"),
    (re.compile(r"\bultimate[\s._-]*(?:edition|cut|version)\b", re.IGNORECASE), "Ultimate Edition"),
    (re.compile(r"\bspecial[\s._-]*(?:edition|cut)\b", re.IGNORECASE), "Special Edition"),
    (re.compile(r"\blimited[\s._-]*(?:edition|cut)\b", re.IGNORECASE), "Limited Edition"),
    (re.compile(r"\bcomplete[\s._-]*(?:edition|cut)\b", re.IGNORECASE), "Complete Edition"),
    (re.compile(r"\bcriterion\b", re.IGNORECASE), "Criterion Collection Edition"),
    (re.compile(r"\b(?:the[\s._-]*)?final[\s._-]*cut\b", re.IGNORECASE), "The Final Cut"),
    (re.compile(r"\bsuper[\s._-]*duper[\s._-]*cut\b", re.IGNORECASE), "Super Duper Cut"),
    (re.compile(r"\bopen[\s._-]*matte\b", re.IGNORECASE), "Open Matte"),
    (re.compile(r"\bimax[\s._-]*(?:edition|version)?\b", re.IGNORECASE), "IMAX Edition"),
    (re.compile(r"\bdespecialized[\s._-]*(?:edition)?\b", re.IGNORECASE), "Despecialized Edition"),
    (re.compile(r"\bworkprint[\s._-]*(?:edition|version)?\b", re.IGNORECASE), "Workprint Edition"),
    (re.compile(r"\bcolorized[\s._-]*(?:version|edition)?\b", re.IGNORECASE), "Colorized Version"),
    (re.compile(r"\bfan[\s._-]*edit\b", re.IGNORECASE), "Fan Edit"),
    (re.compile(r"\brecut[\s._-]*(?:edition)?\b", re.IGNORECASE), "Recut Edition"),
    (re.compile(r"\brestored[\s._-]*(?:edition|version)?\b", re.IGNORECASE), "Restored Edition"),
]


def clean_scene_tags(filename):
    """Strip trailing release group if present (e.g. -FraMeSToR, -FLUX)."""
    base = os.path.splitext(filename)[0]
    # Remove trailing release group after last dash if alphanumeric
    parts = base.rsplit("-", 1)
    if len(parts) == 2 and 2 <= len(parts[1]) <= 15 and not any(k in parts[1].lower() for k in ("cut", "edition", "anniversary", "part")):
        return parts[0]
    return base


def parse_edition_from_filename(filename):
    """
    Extract edition/version title from filename.
    Returns cleaned title string or None.
    """
    if not filename:
        return None

    cleaned_base = clean_scene_tags(filename)

    # 1. Check for explicit {edition-XYZ} or [edition-XYZ]
    for pat in EXPLICIT_TAG_PATTERNS:
        m = pat.search(cleaned_base)
        if m:
            raw = m.group(1).replace(".", " ").replace("_", " ").strip()
            return normalize_edition_name(raw)

    # 2. Check for bracketed edition phrases: e.g. [Director's Cut]
    bracket_m = re.findall(r"[\[\(]([^\(\)\[\]]+)[\]\)]", cleaned_base)
    for chunk in bracket_m:
        matched = match_known_edition(chunk)
        if matched:
            mapped, ignored = apply_custom_mapping(matched)
            return None if ignored else mapped

    # 3. Check full filename for known editions
    matched = match_known_edition(cleaned_base)
    if matched:
        mapped, ignored = apply_custom_mapping(matched)
        return None if ignored else mapped

    return None


def parse_quality_from_filename(filename):
    """
    Extract concise resolution + HDR badge for multi-version quality fallback
    (e.g. '4K DV/HDR', '4K HDR', '4K', '1080p', '720p').
    Returns None if no resolution token is detected.
    """
    if not filename:
        return None

    base = os.path.splitext(filename)[0]
    norm = re.sub(r"[\._]+", " ", base)

    res = None
    if re.search(r"\b(2160p|4k|uhd)\b", norm, re.IGNORECASE):
        res = "4K"
    elif re.search(r"\b1080[pi]\b", norm, re.IGNORECASE):
        res = "1080p"
    elif re.search(r"\b720p\b", norm, re.IGNORECASE):
        res = "720p"
    elif re.search(r"\b(480p|576p|dvdrip)\b", norm, re.IGNORECASE):
        res = "SD"

    if not res:
        return None

    has_dv = bool(re.search(r"\b(dv|dovi|dolby[\s._-]*vision)\b", norm, re.IGNORECASE))
    has_hdr10p = bool(re.search(r"\bhdr10\+", norm, re.IGNORECASE))
    has_hdr = has_hdr10p or bool(re.search(r"\bhdr(?:10)?\b", norm, re.IGNORECASE))

    if has_dv and has_hdr10p:
        return "{} DV/HDR10+".format(res)
    if has_dv and has_hdr:
        return "{} DV/HDR".format(res)
    if has_dv:
        return "{} DV".format(res)
    if has_hdr10p:
        return "{} HDR10+".format(res)
    if has_hdr:
        return "{} HDR".format(res)
    return res


def match_known_edition(text):
    """Matches text against known edition regex list."""
    # Replace dots and underscores with spaces for word boundary matching
    normalized = re.sub(r"[\._]+", " ", text)
    for pat, target in KNOWN_EDITIONS:
        m = pat.search(normalized)
        if m:
            if callable(target):
                return target(m)
            return target
    return None


def clean_edition_text(text):
    """Format an edition name with proper title casing, preserving common acronyms."""
    acronyms = {"imax", "3d", "tv", "us", "uk", "hdr", "sdr", "dv", "uhd", "hd"}
    words = text.split()
    capitalized = []
    for w in words:
        lw = w.lower()
        if lw in acronyms:
            capitalized.append(w.upper())
        elif lw in ("and", "of", "the", "in", "on", "at", "to", "for"):
            capitalized.append(lw)
        else:
            capitalized.append(w.capitalize())
    if capitalized:
        if capitalized[0].lower() not in acronyms:
            capitalized[0] = capitalized[0].capitalize()
    return " ".join(capitalized)


def normalize_edition_name(text):
    """
    Normalize raw edition labels (e.g. 'DIRECTORS_CUT', 'THEATRICAL_EDITION')
    to standard Kodi-friendly display names (e.g. "Director's Cut", 'Theatrical Cut').
    Applies user custom_mappings.json rules if present.
    """
    if not text:
        return None
    cleaned = re.sub(r"[_]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    # Check custom rules against raw/cleaned input first
    mapped_raw, ignored_raw = apply_custom_mapping(text.strip())
    if ignored_raw:
        return None
    if mapped_raw != text.strip():
        return mapped_raw

    mapped_clean, ignored_clean = apply_custom_mapping(cleaned)
    if ignored_clean:
        return None
    if mapped_clean != cleaned:
        return mapped_clean

    matched = match_known_edition(cleaned)
    if matched:
        mapped_match, ignored_match = apply_custom_mapping(matched)
        return None if ignored_match else mapped_match

    if cleaned.isupper():
        return clean_edition_text(cleaned)
    return cleaned

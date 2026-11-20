# -*- coding: utf-8 -*-
"""
Optional user-defined edition rename and ignore rules stored in:
special://profile/addon_data/script.metadata.updatarr/custom_mappings.json
"""
import json
import os
from lib.kodi_compat import translate_path, make_dirs, log

_CACHED_RULES = None


def get_custom_mappings_path():
    addon_data_dir = translate_path("special://profile/addon_data/script.metadata.updatarr/")
    return os.path.join(addon_data_dir, "custom_mappings.json")


def clear_custom_mappings_cache():
    global _CACHED_RULES
    _CACHED_RULES = None


def load_custom_mappings(force_reload=False):
    """
    Load custom rename/ignore rules.
    Returns dict: {"rename": {lower_key: target_str}, "ignore": set(lower_str)}
    """
    global _CACHED_RULES
    if _CACHED_RULES is not None and not force_reload:
        return _CACHED_RULES

    rules = {"rename": {}, "ignore": set()}
    path = get_custom_mappings_path()

    if not os.path.exists(path):
        # Create a clean empty template so users can discover and edit it easily
        try:
            make_dirs(os.path.dirname(path))
            template = {
                "_comment": "Add custom 'rename' pairs (raw_label -> Display Name) or 'ignore' labels.",
                "rename": {},
                "ignore": []
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(template, f, indent=2)
        except Exception:
            pass
        _CACHED_RULES = rules
        return rules

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw_rename = data.get("rename") or {}
            if isinstance(raw_rename, dict):
                for k, v in raw_rename.items():
                    if k and v and isinstance(k, str) and isinstance(v, str):
                        rules["rename"][k.strip().lower()] = v.strip()
            raw_ignore = data.get("ignore") or []
            if isinstance(raw_ignore, list):
                for item in raw_ignore:
                    if item and isinstance(item, str):
                        rules["ignore"].add(item.strip().lower())
    except Exception as e:
        log("Failed to load custom_mappings.json: {}".format(e), "warning")

    _CACHED_RULES = rules
    return rules


def apply_custom_mapping(label):
    """
    Apply custom ignore/rename rules to a label.
    Returns (mapped_label_or_None, was_ignored).
    """
    if not label:
        return None, False
    rules = load_custom_mappings()
    key = label.strip().lower()
    if key in rules["ignore"]:
        return None, True
    if key in rules["rename"]:
        return rules["rename"][key], False
    return label, False

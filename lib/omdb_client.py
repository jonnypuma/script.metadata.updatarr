# -*- coding: utf-8 -*-
"""
OMDb API fallback client with persisted 950 requests/day cap (1,000/day free tier - 5%).
Reads user-configured omdb_api_key or falls back to TMDbHelper's omdb_apikey setting.
"""
import datetime
import json
import os
import threading
import urllib.request
import urllib.parse
import urllib.error
from lib.kodi_compat import translate_path, make_dirs, is_kodi, log
from lib.tmdbhelper_client import RateLimiter


# OMDb free tier: 1,000 req/day -> 1,000 * 0.95 = 950 req/day (-5% safety margin)
OMDB_SAFE_DAILY_LIMIT = 950
OMDB_SAFE_RPS = 10


class OMDbFallbackClient:
    def __init__(self, api_key="", daily_limit=OMDB_SAFE_DAILY_LIMIT):
        self._explicit_key = (api_key or "").strip()
        self._resolved_key = None
        self.daily_limit = max(1, int(daily_limit))
        self._lock = threading.Lock()
        self._cache = {}
        self._rate_limiter = RateLimiter(max_requests=OMDB_SAFE_RPS, window_seconds=1.0)
        self._quota_exhausted_logged = False

    def _get_api_key(self):
        if self._explicit_key:
            return self._explicit_key
        if self._resolved_key is not None:
            return self._resolved_key
        key = ""
        if is_kodi():
            try:
                import xbmcaddon
                tmdbh = xbmcaddon.Addon("plugin.video.themoviedb.helper")
                key = (tmdbh.getSetting("omdb_apikey") or "").strip()
            except Exception:
                key = ""
        self._resolved_key = key
        return key

    def is_available(self):
        return bool(self._get_api_key())

    def _get_quota_path(self):
        addon_data = translate_path("special://profile/addon_data/script.metadata.updatarr/")
        return os.path.join(addon_data, "omdb_quota.json")

    def _today_str(self):
        return datetime.date.today().isoformat()

    def _read_today_count(self):
        path = self._get_quota_path()
        today = self._today_str()
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                return int(data.get("count", 0))
        except Exception:
            pass
        return 0

    def _reserve_quota_slot(self):
        """Thread-safely check and increment today's persisted OMDb request count."""
        with self._lock:
            today = self._today_str()
            count = self._read_today_count()
            if count >= self.daily_limit:
                if not self._quota_exhausted_logged:
                    log("OMDb daily safe limit reached ({}/{}). Skipping further OMDb requests today.".format(
                        count, self.daily_limit
                    ), "warning")
                    self._quota_exhausted_logged = True
                return False
            path = self._get_quota_path()
            try:
                make_dirs(os.path.dirname(path))
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"date": today, "count": count + 1}, f)
            except Exception as e:
                log("Could not persist omdb_quota.json: {}".format(e), "debug")
            return True

    def get_remaining_daily_quota(self):
        with self._lock:
            return max(0, self.daily_limit - self._read_today_count())

    def _fetch(self, params):
        api_key = self._get_api_key()
        if not api_key:
            return None

        cache_key = tuple(sorted(params.items()))
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        if not self._reserve_quota_slot():
            return None

        query_params = dict(params)
        query_params["apikey"] = api_key
        url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(query_params)

        result = None
        try:
            self._rate_limiter.acquire()
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("Response") == "True":
                    result = data
        except urllib.error.HTTPError as e:
            log("OMDb HTTP {}: {}".format(e.code, e.reason), "debug")
        except Exception as e:
            log("OMDb request failed: {}".format(e), "debug")

        with self._lock:
            self._cache[cache_key] = result
        return result

    def _normalize_omdb_data(self, data):
        if not data:
            return None

        def _val(key):
            v = (data.get(key) or "").strip()
            return "" if v in ("", "N/A") else v

        runtime_sec = ""
        rt_raw = _val("Runtime")
        if rt_raw:
            digits = "".join(ch for ch in rt_raw.split()[0] if ch.isdigit())
            if digits:
                runtime_sec = str(int(digits) * 60)

        rated = _val("Rated")
        if rated and not rated.lower().startswith(("rated", "us:")):
            if data.get("Type") == "series":
                rated = "US:" + rated
            else:
                rated = "Rated " + rated

        genres = [g.strip() for g in _val("Genre").split(",") if g.strip()]
        cast = [
            {"name": a.strip(), "role": "", "order": idx, "thumb": ""}
            for idx, a in enumerate(_val("Actors").split(","))
            if a.strip()
        ]
        return {
            "plot": _val("Plot"),
            "mpaa": rated,
            "runtime_sec": runtime_sec,
            "genres": genres,
            "cast": cast,
        }

    def get_by_imdb(self, imdb_id):
        imdb_id = (imdb_id or "").strip()
        if not imdb_id:
            return None
        return self._normalize_omdb_data(self._fetch({"i": imdb_id, "plot": "full"}))

    def get_by_title(self, title, media_type="movie"):
        title = (title or "").strip()
        if not title:
            return None
        omdb_type = "series" if media_type == "tvshow" else "movie"
        return self._normalize_omdb_data(self._fetch({"t": title, "type": omdb_type, "plot": "full"}))

"""
tmdb.py – TheMovieDB (TMDB) API client for fetching media artwork.

Supports both **movies** and **TV shows**, similar to how Radarr/Sonarr
use TMDB for poster artwork.

Features:
  • Smart filename parsing: extracts title + year from common naming
    conventions (``Movie.Name.2020.1080p.BluRay.mkv``,
    ``Show.Name.S02E05.720p.mkv``, etc.)
  • Searches TMDB movie and TV endpoints, picks the best match
  • Downloads poster images and caches them on disk
  • Thread-safe for use from background scan threads

Requires:
  • A free TMDB API key (v3) – obtain one at https://www.themoviedb.org/settings/api
  • ``requests`` and ``Pillow`` Python packages
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_TMDB_API_BASE = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/"
# Poster sizes: w92, w154, w185, w342, w500, w780, original
_POSTER_SIZE_THUMB = "w185"
_POSTER_SIZE_DETAIL = "w342"

_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "media-dup-finder", "posters")
_LOOKUP_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".cache", "media-dup-finder", "tmdb_cache.json")

# Rate-limit: TMDB allows ~40 requests per 10 seconds
_MIN_REQUEST_INTERVAL = 0.25  # seconds between API calls


# ---------------------------------------------------------------------------
# Dataclass for TMDB results
# ---------------------------------------------------------------------------
@dataclass
class TMDBResult:
    """Metadata fetched from TMDB for a single media file."""
    tmdb_id: int = 0
    media_type: str = ""        # "movie" or "tv"
    title: str = ""
    original_title: str = ""
    year: str = ""
    overview: str = ""
    poster_path: str = ""       # TMDB relative path, e.g. "/abc123.jpg"
    vote_average: float = 0.0
    # Local cached poster paths (filled after download)
    poster_thumb_local: str = ""
    poster_detail_local: str = ""


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------
# Common tokens to strip from filenames
_JUNK_TOKENS = re.compile(
    r"""
    \b(
        480[pi]|576[pi]|720[pi]|1080[pi]|2160[pi]|4[kK]|           # resolutions
        HDR(?:10)?|DV|Atmos|TrueHD|DTS[-\s]?(?:HD[-\s]?)?MA?|      # HDR / audio
        BluRay|Blu[-\s]?Ray|BDRip|BRRip|WEB[-\s]?DL|WEB[-\s]?Rip|  # sources
        WEBRip|HDRip|DVDRip|HDTV|PDTV|SDTV|CAM|TS|HC|REMUX|       # sources cont.
        x264|x265|h\.?264|h\.?265|HEVC|AVC|AAC|AC3|EAC3|           # codecs
        FLAC|MP3|DDP?5\.1|7\.1|2\.0|                                # audio
        EXTENDED|REMASTERED|UNRATED|DIRECTORS\.?CUT|                # editions
        PROPER|REPACK|INTERNAL|REAL|COMPLETE|                       # tags
        MULTI|MULTi|DUAL|                                           # language
        NF|AMZN|DSNP|HULU|ATVP|PMTP|HMAX|iT|                      # streaming
        YTS\.[A-Z]+|RARBG|YIFY|EVO|FGT|SPARKS|GECKOS|             # groups
        [\[\(].*?[\]\)]                                             # bracketed
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# TV show patterns: S01E02, 1x02, Season 1, etc.
_TV_PATTERN = re.compile(
    r"""
    (?P<title>.+?)                       # show name (lazy)
    [\s._-]*
    (?:
        [Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,3})   # S01E02
        |(?P<sx>\d{1,2})[xX](?P<ex>\d{1,3})               # 1x02
        |[Ss]eason[\s._-]*(?P<sn>\d{1,2})                  # Season 1
    )
    """,
    re.VERBOSE,
)

# Year pattern: (2020) or .2020.
_YEAR_PATTERN = re.compile(r"[\.\s_\-\(]?((?:19|20)\d{2})[\.\s_\-\)]?")


def parse_filename(filename: str) -> dict:
    """Parse a media filename into structured components.

    Returns dict with keys: title, year, season, episode, is_tv
    """
    stem = Path(filename).stem
    result = {"title": "", "year": "", "season": "", "episode": "", "is_tv": False}

    # Try TV pattern first
    tv_match = _TV_PATTERN.match(stem)
    if tv_match:
        result["is_tv"] = True
        result["title"] = tv_match.group("title")
        result["season"] = (tv_match.group("season")
                            or tv_match.group("sx")
                            or tv_match.group("sn") or "")
        result["episode"] = (tv_match.group("episode")
                             or tv_match.group("ex") or "")
    else:
        # Movie: everything before the year (or the whole stem)
        year_match = _YEAR_PATTERN.search(stem)
        if year_match:
            result["year"] = year_match.group(1)
            result["title"] = stem[:year_match.start()]
        else:
            result["title"] = stem

    # Clean up the title
    title = result["title"]
    title = _JUNK_TOKENS.sub(" ", title)
    title = re.sub(r"[._\-]+", " ", title)       # dots/dashes → spaces
    title = re.sub(r"\s{2,}", " ", title).strip() # collapse whitespace
    result["title"] = title

    return result


# ---------------------------------------------------------------------------
# TMDB API client
# ---------------------------------------------------------------------------
class TMDBClient:
    """Thread-safe TMDB v3 API client with local caching."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self._lookup_cache: dict = {}
        self._load_cache()

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, key: str) -> None:
        self._api_key = key.strip()

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # -- Public API ---------------------------------------------------------
    def lookup(self, filename: str) -> Optional[TMDBResult]:
        """Look up a media file on TMDB by parsing its filename.

        Returns a :class:`TMDBResult` or *None* if nothing matched.
        """
        if not self.is_configured:
            return None

        parsed = parse_filename(filename)
        title = parsed["title"]
        if not title or len(title) < 2:
            return None

        # Check cache
        cache_key = f"{title}|{parsed['year']}|{parsed['is_tv']}"
        cached = self._lookup_cache.get(cache_key)
        if cached is not None:
            return TMDBResult(**cached) if cached else None

        result: Optional[TMDBResult] = None

        if parsed["is_tv"]:
            result = self._search_tv(title)
        else:
            result = self._search_movie(title, parsed["year"])
            # Fallback: try TV search if movie search fails
            if result is None:
                result = self._search_tv(title)

        # Cache the result (or None sentinel)
        self._lookup_cache[cache_key] = (
            result.__dict__ if result else {}
        )
        self._save_cache()

        return result

    def download_poster(self, result: TMDBResult, size: str = "thumb") -> str:
        """Download the poster image and return the local file path.

        *size*: ``"thumb"`` (w185) or ``"detail"`` (w342).
        """
        if not result.poster_path:
            return ""

        tmdb_size = _POSTER_SIZE_THUMB if size == "thumb" else _POSTER_SIZE_DETAIL
        url = f"{_TMDB_IMAGE_BASE}{tmdb_size}{result.poster_path}"

        # Deterministic local filename
        fname = hashlib.md5(url.encode()).hexdigest() + ".jpg"
        local_path = os.path.join(_CACHE_DIR, fname)

        if os.path.exists(local_path):
            return local_path

        os.makedirs(_CACHE_DIR, exist_ok=True)
        try:
            self._throttle()
            req = Request(url, headers={"User-Agent": "MediaDupFinder/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = resp.read()
            with open(local_path, "wb") as f:
                f.write(data)
            return local_path
        except Exception:
            return ""

    def get_poster_tk_image(self, result: TMDBResult, size: str = "thumb"):
        """Download poster and return a ``tkinter.PhotoImage``-compatible
        ``ImageTk.PhotoImage`` object, or *None*.

        Caller must keep a reference to the returned object to prevent GC.
        """
        local = self.download_poster(result, size)
        if not local:
            return None
        try:
            from PIL import Image, ImageTk
            img = Image.open(local)
            if size == "thumb":
                img.thumbnail((92, 138))
            else:
                img.thumbnail((185, 278))
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # -- Internal API calls -------------------------------------------------
    def _search_movie(self, title: str, year: str = "") -> Optional[TMDBResult]:
        params = {"query": title}
        if year:
            params["year"] = year
        data = self._api_get("/search/movie", params)
        if not data:
            return None
        results = data.get("results", [])
        if not results:
            return None
        best = results[0]
        release = best.get("release_date", "")
        return TMDBResult(
            tmdb_id=best.get("id", 0),
            media_type="movie",
            title=best.get("title", ""),
            original_title=best.get("original_title", ""),
            year=release[:4] if release else "",
            overview=best.get("overview", "")[:200],
            poster_path=best.get("poster_path", "") or "",
            vote_average=best.get("vote_average", 0),
        )

    def _search_tv(self, title: str) -> Optional[TMDBResult]:
        data = self._api_get("/search/tv", {"query": title})
        if not data:
            return None
        results = data.get("results", [])
        if not results:
            return None
        best = results[0]
        first_air = best.get("first_air_date", "")
        return TMDBResult(
            tmdb_id=best.get("id", 0),
            media_type="tv",
            title=best.get("name", ""),
            original_title=best.get("original_name", ""),
            year=first_air[:4] if first_air else "",
            overview=best.get("overview", "")[:200],
            poster_path=best.get("poster_path", "") or "",
            vote_average=best.get("vote_average", 0),
        )

    def _api_get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        if not self._api_key:
            return None
        self._throttle()
        url_params = f"api_key={self._api_key}"
        for k, v in (params or {}).items():
            from urllib.parse import quote
            url_params += f"&{k}={quote(str(v))}"
        url = f"{_TMDB_API_BASE}{endpoint}?{url_params}"
        try:
            req = Request(url, headers={"User-Agent": "MediaDupFinder/1.0"})
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    # -- Rate limiting ------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.time()

    # -- Disk cache ---------------------------------------------------------
    def _load_cache(self) -> None:
        try:
            with open(_LOOKUP_CACHE_FILE) as f:
                self._lookup_cache = json.load(f)
        except Exception:
            self._lookup_cache = {}

    def _save_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(_LOOKUP_CACHE_FILE), exist_ok=True)
            with open(_LOOKUP_CACHE_FILE, "w") as f:
                json.dump(self._lookup_cache, f, indent=1)
        except Exception:
            pass

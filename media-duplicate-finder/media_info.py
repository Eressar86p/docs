"""
media_info.py – Extract metadata from media files using ffprobe and Pillow.

Provides:
    MediaInfo   : dataclass holding all extracted metadata
    get_info()  : analyse a single file and return a MediaInfo
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Supported extensions
# ---------------------------------------------------------------------------
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".svg", ".ico", ".heic", ".heif", ".raw", ".cr2",
    ".nef", ".arw",
}
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".ogv", ".ts", ".vob",
}
AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aac", ".ogg", ".wma", ".m4a",
    ".opus", ".aiff", ".alac", ".ape",
}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

HAS_FFPROBE = shutil.which("ffprobe") is not None


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass
class StreamInfo:
    """Represents one stream inside a media container."""
    index: int = 0
    codec_type: str = ""        # video / audio / subtitle
    codec_name: str = ""
    language: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_rate: Optional[int] = None
    title: str = ""


@dataclass
class MediaInfo:
    """All metadata we care about for a single media file."""
    path: str = ""
    filename: str = ""
    extension: str = ""
    media_type: str = ""            # image / video / audio
    size_bytes: int = 0
    content_hash: str = ""          # SHA-256 of full file contents
    partial_hash: str = ""          # SHA-256 of first+last 64 KiB (fast)
    creation_date: str = ""
    modification_date: str = ""
    # Image / video dimensions
    width: Optional[int] = None
    height: Optional[int] = None
    resolution_label: str = ""      # e.g. "1920x1080"
    # Duration (video / audio)
    duration_secs: Optional[float] = None
    duration_label: str = ""
    # Overall bitrate
    bit_rate: Optional[int] = None
    bit_rate_label: str = ""
    # Container format
    format_name: str = ""
    # Streams
    video_streams: List[StreamInfo] = field(default_factory=list)
    audio_streams: List[StreamInfo] = field(default_factory=list)
    subtitle_streams: List[StreamInfo] = field(default_factory=list)
    # Perceptual hash for images (hex string) – populated when Pillow is
    # available and the file is an image.
    perceptual_hash: str = ""
    # TMDB metadata (populated separately by tmdb.TMDBClient)
    tmdb_title: str = ""
    tmdb_year: str = ""
    tmdb_type: str = ""            # "movie" or "tv"
    tmdb_poster_path: str = ""     # TMDB relative poster path
    tmdb_overview: str = ""
    tmdb_rating: float = 0.0
    tmdb_id: int = 0


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------
_PARTIAL_CHUNK = 64 * 1024  # 64 KiB


def _sha256_full(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_partial(path: str) -> str:
    """Hash the first and last 64 KiB – fast fingerprint."""
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(_PARTIAL_CHUNK))
        if size > _PARTIAL_CHUNK * 2:
            f.seek(-_PARTIAL_CHUNK, 2)
            h.update(f.read(_PARTIAL_CHUNK))
    # Also mix in the file size so that files with identical heads/tails but
    # different lengths don't collide.
    h.update(str(size).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Perceptual hashing (images only, optional)
# ---------------------------------------------------------------------------
def _perceptual_hash(path: str) -> str:
    """Return a 64-bit average-hash hex string for an image, or '' on failure."""
    try:
        from PIL import Image
        img = Image.open(path).convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return f"{int(bits, 2):016x}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# ffprobe helper
# ---------------------------------------------------------------------------
def _ffprobe(path: str) -> Optional[dict]:
    if not HAS_FFPROBE:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _classify(ext: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "unknown"


def _fmt_duration(secs: Optional[float]) -> str:
    if secs is None:
        return ""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_bitrate(bps: Optional[int]) -> str:
    if bps is None:
        return ""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} kbps"
    return f"{bps} bps"


def _parse_stream(raw: dict) -> StreamInfo:
    tags = raw.get("tags", {})
    br = raw.get("bit_rate")
    return StreamInfo(
        index=int(raw.get("index", 0)),
        codec_type=raw.get("codec_type", ""),
        codec_name=raw.get("codec_name", ""),
        language=tags.get("language", ""),
        width=int(raw["width"]) if "width" in raw else None,
        height=int(raw["height"]) if "height" in raw else None,
        sample_rate=int(raw["sample_rate"]) if "sample_rate" in raw else None,
        channels=int(raw["channels"]) if "channels" in raw else None,
        bit_rate=int(br) if br else None,
        title=tags.get("title", ""),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_info(path: str, full_hash: bool = False) -> MediaInfo:
    """Analyse *path* and return a populated :class:`MediaInfo`.

    Parameters
    ----------
    path:
        Absolute or relative path to a media file.
    full_hash:
        If *True*, compute the full-file SHA-256 (slower for large files).
        The fast partial hash is always computed.
    """
    p = Path(path).resolve()
    ext = p.suffix.lower()
    stat = p.stat()

    info = MediaInfo(
        path=str(p),
        filename=p.name,
        extension=ext,
        media_type=_classify(ext),
        size_bytes=stat.st_size,
        creation_date=_fmt_ts(stat.st_ctime),
        modification_date=_fmt_ts(stat.st_mtime),
    )

    # Hashes
    info.partial_hash = _sha256_partial(str(p))
    if full_hash:
        info.content_hash = _sha256_full(str(p))

    # Perceptual hash for images
    if info.media_type == "image":
        info.perceptual_hash = _perceptual_hash(str(p))

    # ffprobe metadata
    probe = _ffprobe(str(p))
    if probe:
        fmt = probe.get("format", {})
        info.format_name = fmt.get("format_long_name", fmt.get("format_name", ""))
        dur = fmt.get("duration")
        if dur:
            info.duration_secs = float(dur)
            info.duration_label = _fmt_duration(info.duration_secs)
        br = fmt.get("bit_rate")
        if br:
            info.bit_rate = int(br)
            info.bit_rate_label = _fmt_bitrate(info.bit_rate)

        for raw_stream in probe.get("streams", []):
            si = _parse_stream(raw_stream)
            if si.codec_type == "video":
                info.video_streams.append(si)
                if si.width and si.height and info.width is None:
                    info.width = si.width
                    info.height = si.height
            elif si.codec_type == "audio":
                info.audio_streams.append(si)
            elif si.codec_type == "subtitle":
                info.subtitle_streams.append(si)
    else:
        # Fallback: try Pillow for image dimensions
        if info.media_type == "image":
            try:
                from PIL import Image
                with Image.open(str(p)) as img:
                    info.width, info.height = img.size
            except Exception:
                pass

    if info.width and info.height:
        info.resolution_label = f"{info.width}x{info.height}"

    return info


def _fmt_ts(ts: float) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def is_media_file(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS

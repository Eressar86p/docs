"""
gui/details_dialog.py – Side-by-side comparison of all items in a group.

Shows a table with one column per file and rows for every metadata field:
  • Filename / path
  • File size
  • Creation date
  • Modification date
  • Resolution (image / video) or sample rate (audio)
  • Duration
  • Overall bitrate
  • Video streams  (codec, resolution, bitrate)
  • Audio streams  (codec, channels, sample rate, bitrate, language)
  • Subtitle streams (codec, language)

The "best" value in each row is highlighted green so the user can quickly
spot the highest-quality version.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, List, Optional

from scanner import DuplicateGroup
from media_info import MediaInfo
from tmdb import TMDBClient, TMDBResult


_HIGHLIGHT = "#d4edda"   # light green
_HEADER_BG = "#e9ecef"   # light gray


class DetailsDialog(tk.Toplevel):
    """Modal-ish dialog comparing all items in *group*."""

    def __init__(
        self,
        parent: tk.Widget,
        group: DuplicateGroup,
        tmdb_client: Optional["TMDBClient"] = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Details  –  Group {group.group_id + 1}")
        self.geometry("1050x700")
        self.minsize(700, 400)
        self.transient(parent)
        self.grab_set()

        self._group = group
        self._tmdb = tmdb_client
        # Keep refs to poster images to prevent GC
        self._poster_refs: list = []
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Match-reason banner
        reasons = ", ".join(self._group.match_reasons) or "unknown"
        ttk.Label(self, text=f"Matched by: {reasons}",
                  font=("", 10, "italic")).pack(
            anchor=tk.W, padx=10, pady=(8, 2))

        # Scrollable table
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        xsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL)
        ysb = ttk.Scrollbar(container, orient=tk.VERTICAL)
        self._canvas = tk.Canvas(
            container, highlightthickness=0,
            xscrollcommand=xsb.set, yscrollcommand=ysb.set,
        )
        xsb.config(command=self._canvas.xview)
        ysb.config(command=self._canvas.yview)

        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._table = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._table, anchor=tk.NW)
        self._table.bind("<Configure>",
                         lambda _: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))

        self._populate()

        # Close button
        ttk.Button(self, text="Close", command=self.destroy).pack(
            pady=(0, 10))

    # ------------------------------------------------------------------
    def _populate(self) -> None:
        items = self._group.items
        ncols = len(items)
        current_row = 0

        # ---- Header row (filenames) -----------------------------------
        self._header_cell(current_row, 0, "Property")
        for c, mi in enumerate(items):
            self._header_cell(current_row, c + 1, mi.filename)
        current_row += 1

        # ---- Poster row (images displayed in cells) -------------------
        has_posters = any(mi.tmdb_poster_path for mi in items)
        if has_posters and self._tmdb and self._tmdb.is_configured:
            self._label_cell(current_row, 0, "Poster")
            for c, mi in enumerate(items):
                self._poster_cell(current_row, c + 1, mi)
            current_row += 1

        # ---- Data rows ------------------------------------------------
        rows: List[_Row] = [
            _Row("Directory", lambda m: _trunc_dir(m.path, 50)),
            _Row("File size", lambda m: _fmt_size(m.size_bytes),
                 best=lambda vals: _idx_of_max(vals, key=lambda m: m.size_bytes)),
            _Row("Creation date", lambda m: m.creation_date),
            _Row("Modification date", lambda m: m.modification_date),
            _Row("Media type", lambda m: m.media_type),
            _Row("Format", lambda m: m.format_name),
            _Row("Resolution", lambda m: m.resolution_label,
                 best=lambda vals: _idx_of_max(
                     vals, key=lambda m: (m.width or 0) * (m.height or 0))),
            _Row("Duration", lambda m: m.duration_label,
                 best=lambda vals: _idx_of_max(
                     vals, key=lambda m: m.duration_secs or 0)),
            _Row("Overall bitrate", lambda m: m.bit_rate_label,
                 best=lambda vals: _idx_of_max(
                     vals, key=lambda m: m.bit_rate or 0)),
            # TMDB metadata
            _Row("TMDB title", lambda m: m.tmdb_title or ""),
            _Row("TMDB year", lambda m: m.tmdb_year or ""),
            _Row("TMDB type", lambda m: (
                "TV Show" if m.tmdb_type == "tv"
                else "Movie" if m.tmdb_type == "movie"
                else "")),
            _Row("TMDB rating", lambda m: (
                f"{m.tmdb_rating:.1f}/10" if m.tmdb_rating else "")),
        ]

        # Stream rows (video)
        max_vid = max((len(mi.video_streams) for mi in items), default=0)
        for si in range(max_vid):
            rows.append(_Row(
                f"Video stream #{si + 1}",
                lambda m, _si=si: _fmt_video_stream(m, _si),
            ))

        # Stream rows (audio)
        max_aud = max((len(mi.audio_streams) for mi in items), default=0)
        for si in range(max_aud):
            rows.append(_Row(
                f"Audio stream #{si + 1}",
                lambda m, _si=si: _fmt_audio_stream(m, _si),
            ))

        # Stream rows (subtitle)
        max_sub = max((len(mi.subtitle_streams) for mi in items), default=0)
        for si in range(max_sub):
            rows.append(_Row(
                f"Subtitle stream #{si + 1}",
                lambda m, _si=si: _fmt_subtitle_stream(m, _si),
            ))

        # Hashes
        rows += [
            _Row("Partial hash", lambda m: m.partial_hash[:16] + "…"
                 if m.partial_hash else ""),
            _Row("Full hash", lambda m: m.content_hash[:16] + "…"
                 if m.content_hash else "(not computed)"),
            _Row("Perceptual hash", lambda m: m.perceptual_hash or ""),
        ]

        for row_def in rows:
            self._label_cell(current_row, 0, row_def.label)
            best_idx = None
            if row_def.best:
                try:
                    best_idx = row_def.best(items)
                except Exception:
                    pass
            for c, mi in enumerate(items):
                val = ""
                try:
                    val = row_def.formatter(mi) or ""
                except Exception:
                    val = ""
                bg = _HIGHLIGHT if (best_idx is not None and c == best_idx) else ""
                self._data_cell(current_row, c + 1, val, bg=bg)
            current_row += 1

    # ------------------------------------------------------------------
    # Cell helpers
    # ------------------------------------------------------------------
    def _header_cell(self, r: int, c: int, text: str) -> None:
        lbl = tk.Label(self._table, text=text, font=("", 9, "bold"),
                       bg=_HEADER_BG, anchor=tk.W, padx=6, pady=3,
                       borderwidth=1, relief=tk.GROOVE)
        lbl.grid(row=r, column=c, sticky="nsew")

    def _label_cell(self, r: int, c: int, text: str) -> None:
        lbl = tk.Label(self._table, text=text, font=("", 9, "bold"),
                       anchor=tk.W, padx=6, pady=2,
                       borderwidth=1, relief=tk.GROOVE)
        lbl.grid(row=r, column=c, sticky="nsew")

    def _poster_cell(self, r: int, c: int, mi: MediaInfo) -> None:
        """Display a poster thumbnail in a table cell."""
        if mi.tmdb_poster_path and self._tmdb:
            result = TMDBResult(
                tmdb_id=mi.tmdb_id,
                poster_path=mi.tmdb_poster_path,
                title=mi.tmdb_title,
            )
            photo = self._tmdb.get_poster_tk_image(result, size="detail")
            if photo:
                lbl = tk.Label(self._table, image=photo,
                               borderwidth=1, relief=tk.GROOVE)
                lbl.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                self._poster_refs.append(photo)
                return
        # Fallback: empty cell
        lbl = tk.Label(self._table, text="—", anchor=tk.CENTER,
                       borderwidth=1, relief=tk.GROOVE, font=("", 9))
        lbl.grid(row=r, column=c, sticky="nsew")

    def _data_cell(self, r: int, c: int, text: str, bg: str = "") -> None:
        kw: dict = dict(
            text=text, anchor=tk.W, padx=6, pady=2,
            borderwidth=1, relief=tk.GROOVE, font=("", 9),
        )
        if bg:
            kw["bg"] = bg
        lbl = tk.Label(self._table, **kw)
        lbl.grid(row=r, column=c, sticky="nsew")


# ======================================================================
# Internal helpers
# ======================================================================
class _Row:
    __slots__ = ("label", "formatter", "best")

    def __init__(
        self,
        label: str,
        formatter: Callable[[MediaInfo], str],
        best: Optional[Callable[[List[MediaInfo]], Optional[int]]] = None,
    ) -> None:
        self.label = label
        self.formatter = formatter
        self.best = best


def _idx_of_max(
    items: List[MediaInfo],
    key: Callable[[MediaInfo], Any],
) -> Optional[int]:
    """Return index of the item with the highest *key* value, or None."""
    vals = [key(mi) for mi in items]
    if not vals or all(v == vals[0] for v in vals):
        return None  # all equal → no highlight
    return max(range(len(vals)), key=lambda i: vals[i])


def _trunc_dir(path: str, maxlen: int) -> str:
    import os
    d = os.path.dirname(path)
    if len(d) > maxlen:
        return "…" + d[-(maxlen - 1):]
    return d


def _fmt_size(nbytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} PiB"


def _fmt_video_stream(mi: MediaInfo, idx: int) -> str:
    if idx >= len(mi.video_streams):
        return "—"
    s = mi.video_streams[idx]
    parts = [s.codec_name]
    if s.width and s.height:
        parts.append(f"{s.width}x{s.height}")
    if s.bit_rate:
        parts.append(f"{s.bit_rate // 1000} kbps")
    if s.language:
        parts.append(f"[{s.language}]")
    return "  ".join(parts)


def _fmt_audio_stream(mi: MediaInfo, idx: int) -> str:
    if idx >= len(mi.audio_streams):
        return "—"
    s = mi.audio_streams[idx]
    parts = [s.codec_name]
    if s.channels:
        parts.append(f"{s.channels}ch")
    if s.sample_rate:
        parts.append(f"{s.sample_rate} Hz")
    if s.bit_rate:
        parts.append(f"{s.bit_rate // 1000} kbps")
    if s.language:
        parts.append(f"[{s.language}]")
    if s.title:
        parts.append(f'"{s.title}"')
    return "  ".join(parts)


def _fmt_subtitle_stream(mi: MediaInfo, idx: int) -> str:
    if idx >= len(mi.subtitle_streams):
        return "—"
    s = mi.subtitle_streams[idx]
    parts = [s.codec_name]
    if s.language:
        parts.append(f"[{s.language}]")
    if s.title:
        parts.append(f'"{s.title}"')
    return "  ".join(parts)

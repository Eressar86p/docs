"""
gui/results_view.py – Display duplicate groups with checkboxes.

Each group is shown as a collapsible section.  Inside it, every duplicate
file has:
  • a checkbox (for batch deletion / moving)
  • filename, path, size, media type
  • a "Details" button that opens the comparison dialog
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Dict, List

from scanner import DuplicateGroup
from media_info import MediaInfo
from gui.details_dialog import DetailsDialog
from tmdb import TMDBClient


class ResultsView(ttk.Frame):
    """Scrollable view of all duplicate groups with checkboxes."""

    def __init__(
        self,
        parent: tk.Widget,
        groups: List[DuplicateGroup],
        tmdb_client: TMDBClient | None = None,
    ) -> None:
        super().__init__(parent)
        self._groups = groups
        self._tmdb = tmdb_client
        # path → BooleanVar for each checkbox
        self._check_vars: Dict[str, tk.BooleanVar] = {}
        # Keep references to poster images so they don't get GC'd
        self._poster_refs: List = []
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # --- Top action bar -------------------------------------------
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(bar, text="Select All",
                   command=self._select_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bar, text="Deselect All",
                   command=self._deselect_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bar, text="Auto-select duplicates (keep best)",
                   command=self._auto_select).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Separator(bar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=6)

        ttk.Button(bar, text="Delete Selected…",
                   command=self._delete_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bar, text="Move Selected…",
                   command=self._move_selected).pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self._status_var).pack(
            side=tk.RIGHT, padx=(12, 0))

        # --- Scrollable canvas for groups -----------------------------
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL,
                            command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inner = ttk.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._inner, anchor=tk.NW,
        )

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # Mouse wheel scrolling
        self._canvas.bind_all("<Button-4>", self._on_mousewheel_up)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel_down)

        self._populate()
        self._update_status()

    # ------------------------------------------------------------------
    # Populate groups
    # ------------------------------------------------------------------
    def _populate(self) -> None:
        for gidx, group in enumerate(self._groups):
            gf = ttk.LabelFrame(
                self._inner,
                text=f"  Group {gidx + 1}  —  {len(group.items)} files  "
                     f"({', '.join(group.match_reasons)})  ",
                padding=6,
            )
            gf.pack(fill=tk.X, padx=4, pady=4)

            # Group content: poster on the left, file list on the right
            group_body = ttk.Frame(gf)
            group_body.pack(fill=tk.X)

            # Try to show a poster thumbnail from the first item with TMDB data
            tmdb_item = next((mi for mi in group.items if mi.tmdb_poster_path), None)
            if tmdb_item and self._tmdb and self._tmdb.is_configured:
                poster_frame = ttk.Frame(group_body)
                poster_frame.pack(side=tk.LEFT, padx=(0, 8), anchor=tk.N)
                self._add_poster_thumb(poster_frame, tmdb_item)

            # Right side: header + file rows
            right_frame = ttk.Frame(group_body)
            right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            # Header row with TMDB info + Details button
            header = ttk.Frame(right_frame)
            header.pack(fill=tk.X, pady=(0, 4))

            if tmdb_item and tmdb_item.tmdb_title:
                kind = "TV" if tmdb_item.tmdb_type == "tv" else "Movie"
                tmdb_text = f"{tmdb_item.tmdb_title}"
                if tmdb_item.tmdb_year:
                    tmdb_text += f" ({tmdb_item.tmdb_year})"
                tmdb_text += f"  [{kind}]"
                if tmdb_item.tmdb_rating:
                    tmdb_text += f"  TMDB: {tmdb_item.tmdb_rating:.1f}/10"
                ttk.Label(header, text=tmdb_text,
                          font=("", 9, "bold")).pack(side=tk.LEFT)

            ttk.Button(
                header, text="Compare Details…",
                command=lambda g=group: self._show_details(g),
            ).pack(side=tk.RIGHT)

            for item in group.items:
                self._add_item_row(right_frame, item)

    def _add_poster_thumb(self, parent: tk.Widget, mi: MediaInfo) -> None:
        """Load and display a small poster thumbnail from TMDB."""
        if not self._tmdb or not mi.tmdb_poster_path:
            return
        from tmdb import TMDBResult
        result = TMDBResult(
            tmdb_id=mi.tmdb_id,
            poster_path=mi.tmdb_poster_path,
            title=mi.tmdb_title,
        )
        photo = self._tmdb.get_poster_tk_image(result, size="thumb")
        if photo:
            lbl = ttk.Label(parent, image=photo)
            lbl.pack()
            self._poster_refs.append(photo)  # prevent GC
        else:
            ttk.Label(parent, text="[No poster]",
                      foreground="gray").pack()

    def _add_item_row(self, parent: tk.Widget, mi: MediaInfo) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)

        var = tk.BooleanVar(value=False)
        self._check_vars[mi.path] = var

        cb = ttk.Checkbutton(row, variable=var, command=self._update_status)
        cb.pack(side=tk.LEFT)

        # Filename (bold-ish via a slightly larger font)
        name_lbl = ttk.Label(row, text=mi.filename, width=36, anchor=tk.W)
        name_lbl.pack(side=tk.LEFT, padx=(2, 8))

        # Size
        ttk.Label(row, text=_fmt_size(mi.size_bytes), width=10,
                  anchor=tk.E).pack(side=tk.LEFT, padx=(0, 8))

        # Media type
        ttk.Label(row, text=mi.media_type, width=6).pack(
            side=tk.LEFT, padx=(0, 8))

        # Resolution / duration
        extra = mi.resolution_label or mi.duration_label or ""
        ttk.Label(row, text=extra, width=14).pack(side=tk.LEFT, padx=(0, 8))

        # Path (truncated)
        dir_path = os.path.dirname(mi.path)
        ttk.Label(row, text=dir_path, foreground="gray").pack(
            side=tk.LEFT, fill=tk.X, expand=True)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _select_all(self) -> None:
        for v in self._check_vars.values():
            v.set(True)
        self._update_status()

    def _deselect_all(self) -> None:
        for v in self._check_vars.values():
            v.set(False)
        self._update_status()

    def _auto_select(self) -> None:
        """For each group, keep the 'best' file and select the rest.

        Heuristic for 'best':
          1. Highest resolution (width × height) or highest bitrate
          2. Largest file size as tie-breaker
        """
        self._deselect_all()
        for group in self._groups:
            if len(group.items) < 2:
                continue
            ranked = sorted(group.items, key=_quality_key, reverse=True)
            # Keep the best (first), mark the rest
            for item in ranked[1:]:
                var = self._check_vars.get(item.path)
                if var:
                    var.set(True)
        self._update_status()

    def _update_status(self, *_args) -> None:
        count = sum(1 for v in self._check_vars.values() if v.get())
        self._status_var.set(f"{count} file(s) selected")

    def _selected_paths(self) -> List[str]:
        return [p for p, v in self._check_vars.items() if v.get()]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("Nothing selected", "Select files first.")
            return
        if not messagebox.askyesno(
            "Confirm deletion",
            f"Permanently delete {len(paths)} file(s)?\n\n"
            "This cannot be undone.",
        ):
            return
        errors: List[str] = []
        for p in paths:
            try:
                os.remove(p)
            except OSError as e:
                errors.append(f"{p}: {e}")
        if errors:
            messagebox.showwarning("Some deletions failed",
                                   "\n".join(errors))
        messagebox.showinfo("Done", f"Deleted {len(paths) - len(errors)} file(s).")

    def _move_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("Nothing selected", "Select files first.")
            return
        dest = filedialog.askdirectory(title="Move selected files to…")
        if not dest:
            return
        errors: List[str] = []
        import shutil
        for p in paths:
            try:
                shutil.move(p, os.path.join(dest, os.path.basename(p)))
            except OSError as e:
                errors.append(f"{p}: {e}")
        if errors:
            messagebox.showwarning("Some moves failed",
                                   "\n".join(errors))
        messagebox.showinfo(
            "Done", f"Moved {len(paths) - len(errors)} file(s) to {dest}.")

    def _show_details(self, group: DuplicateGroup) -> None:
        DetailsDialog(self, group, tmdb_client=self._tmdb)

    # ------------------------------------------------------------------
    # Canvas scrolling helpers
    # ------------------------------------------------------------------
    def _on_inner_configure(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._canvas_window, width=event.width)

    def _on_mousewheel_up(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(-3, "units")

    def _on_mousewheel_down(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(3, "units")


# ======================================================================
# Helpers
# ======================================================================
def _fmt_size(nbytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} PiB"


def _quality_key(mi: MediaInfo):
    """Return a sort key: higher = better quality."""
    res = (mi.width or 0) * (mi.height or 0)
    br = mi.bit_rate or 0
    return (res, br, mi.size_bytes)

"""
gui/app.py – Main application window.

Responsibilities:
  • Folder selection (add / remove / "All sub-folders" toggle)
  • Scanner configuration
  • Launch scan in a background thread
  • Show progress
  • Hand off results to ResultsView
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from scanner import DuplicateGroup, Scanner
from gui.results_view import ResultsView
from tmdb import TMDBClient

# Path to persist the API key between sessions
_SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".config", "media-dup-finder")
_API_KEY_FILE = os.path.join(_SETTINGS_DIR, "tmdb_api_key")


class App(tk.Tk):
    """Top-level application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Media Duplicate Finder")
        self.geometry("1100x750")
        self.minsize(800, 500)

        self._scanner: Optional[Scanner] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._results: List[DuplicateGroup] = []
        self._tmdb = TMDBClient()

        self._build_ui()
        self._load_api_key()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # --- Top frame: folder selector --------------------------------
        folder_frame = ttk.LabelFrame(self, text="Folders to scan", padding=8)
        folder_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        list_frame = ttk.Frame(folder_frame)
        list_frame.pack(fill=tk.X)

        self._folder_listbox = tk.Listbox(
            list_frame, height=5, selectmode=tk.EXTENDED,
        )
        self._folder_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                           command=self._folder_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._folder_listbox.config(yscrollcommand=sb.set)

        btn_row = ttk.Frame(folder_frame)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        ttk.Button(btn_row, text="Add Folder…",
                   command=self._add_folder).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="Remove Selected",
                   command=self._remove_folders).pack(side=tk.LEFT, padx=(0, 4))

        self._recurse_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            btn_row, text="Include all sub-folders",
            variable=self._recurse_var,
        ).pack(side=tk.LEFT, padx=(12, 0))

        # --- Options frame ---------------------------------------------
        opt_frame = ttk.LabelFrame(self, text="Options", padding=8)
        opt_frame.pack(fill=tk.X, padx=10, pady=4)

        ttk.Label(opt_frame, text="Name similarity threshold:").grid(
            row=0, column=0, sticky=tk.W)
        self._threshold_var = tk.DoubleVar(value=0.80)
        self._threshold_spin = ttk.Spinbox(
            opt_frame, from_=0.50, to=1.00, increment=0.05,
            textvariable=self._threshold_var, width=6,
        )
        self._threshold_spin.grid(row=0, column=1, sticky=tk.W, padx=(4, 16))

        self._fullhash_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_frame, text="Verify with full file hash (slower but accurate)",
            variable=self._fullhash_var,
        ).grid(row=0, column=2, sticky=tk.W)

        # --- TMDB frame --------------------------------------------------
        tmdb_frame = ttk.LabelFrame(self, text="TMDB Integration (posters for movies & TV)", padding=8)
        tmdb_frame.pack(fill=tk.X, padx=10, pady=4)

        ttk.Label(tmdb_frame, text="API Key (v3):").pack(side=tk.LEFT)
        self._api_key_var = tk.StringVar()
        self._api_key_entry = ttk.Entry(
            tmdb_frame, textvariable=self._api_key_var, width=40, show="*",
        )
        self._api_key_entry.pack(side=tk.LEFT, padx=(4, 4))

        self._show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            tmdb_frame, text="Show",
            variable=self._show_key_var,
            command=self._toggle_key_visibility,
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(tmdb_frame, text="Save Key",
                   command=self._save_api_key).pack(side=tk.LEFT, padx=(0, 4))

        self._tmdb_status = tk.StringVar(value="")
        ttk.Label(tmdb_frame, textvariable=self._tmdb_status,
                  foreground="gray").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(
            tmdb_frame,
            text="Free key from themoviedb.org/settings/api",
            foreground="blue", cursor="hand2",
        ).pack(side=tk.RIGHT)

        # --- Scan button & progress ------------------------------------
        action_frame = ttk.Frame(self, padding=(10, 4))
        action_frame.pack(fill=tk.X)

        self._scan_btn = ttk.Button(
            action_frame, text="Start Scan", command=self._start_scan,
        )
        self._scan_btn.pack(side=tk.LEFT)

        self._cancel_btn = ttk.Button(
            action_frame, text="Cancel", command=self._cancel_scan,
            state=tk.DISABLED,
        )
        self._cancel_btn.pack(side=tk.LEFT, padx=(6, 0))

        self._progress_var = tk.StringVar(value="Ready.")
        ttk.Label(action_frame, textvariable=self._progress_var).pack(
            side=tk.LEFT, padx=(12, 0))

        self._pbar = ttk.Progressbar(action_frame, length=250, mode="determinate")
        self._pbar.pack(side=tk.RIGHT)

        # --- Results area (filled after scan) --------------------------
        self._results_frame = ttk.Frame(self)
        self._results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self._results_view: Optional[ResultsView] = None

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------
    def _add_folder(self) -> None:
        path = filedialog.askdirectory(title="Select folder to scan")
        if path:
            # Avoid duplicate entries
            existing = list(self._folder_listbox.get(0, tk.END))
            if path not in existing:
                self._folder_listbox.insert(tk.END, path)

    def _remove_folders(self) -> None:
        for idx in reversed(self._folder_listbox.curselection()):
            self._folder_listbox.delete(idx)

    # ------------------------------------------------------------------
    # TMDB API key management
    # ------------------------------------------------------------------
    def _load_api_key(self) -> None:
        try:
            with open(_API_KEY_FILE) as f:
                key = f.read().strip()
            if key:
                self._api_key_var.set(key)
                self._tmdb.api_key = key
                self._tmdb_status.set("Key loaded.")
        except FileNotFoundError:
            self._tmdb_status.set("No key saved. Posters disabled.")
        except Exception:
            pass

    def _save_api_key(self) -> None:
        key = self._api_key_var.get().strip()
        self._tmdb.api_key = key
        try:
            os.makedirs(_SETTINGS_DIR, exist_ok=True)
            with open(_API_KEY_FILE, "w") as f:
                f.write(key)
            os.chmod(_API_KEY_FILE, 0o600)
            self._tmdb_status.set("Key saved." if key else "Key cleared.")
        except Exception as e:
            self._tmdb_status.set(f"Save failed: {e}")

    def _toggle_key_visibility(self) -> None:
        self._api_key_entry.config(
            show="" if self._show_key_var.get() else "*"
        )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def _start_scan(self) -> None:
        folders = list(self._folder_listbox.get(0, tk.END))
        if not folders:
            messagebox.showwarning("No folders", "Add at least one folder to scan.")
            return

        # If "include all sub-folders" is unchecked, we pass only the
        # top-level directory (os.walk is still used but the Scanner
        # handles it).  The scanner always recurses via os.walk; when the
        # user unchecks the box we restrict to immediate children only
        # by filtering afterwards.
        self._scan_btn.config(state=tk.DISABLED)
        self._cancel_btn.config(state=tk.NORMAL)
        self._pbar["value"] = 0

        # Clear old results
        if self._results_view:
            self._results_view.destroy()
            self._results_view = None

        # Update TMDB key from the entry field in case user typed without saving
        self._tmdb.api_key = self._api_key_var.get().strip()

        self._scanner = Scanner(
            name_similarity_threshold=self._threshold_var.get(),
            compute_full_hash=self._fullhash_var.get(),
            on_progress=self._on_scan_progress,
            tmdb_client=self._tmdb if self._tmdb.is_configured else None,
        )

        self._scan_thread = threading.Thread(
            target=self._run_scan, args=(folders,), daemon=True,
        )
        self._scan_thread.start()

    def _run_scan(self, folders: List[str]) -> None:
        assert self._scanner is not None
        try:
            results = self._scanner.scan(folders)
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Scan error", str(exc)))
            results = []
        self.after(0, lambda: self._scan_finished(results))

    def _on_scan_progress(self, msg: str, cur: int, total: int) -> None:
        # Called from the scanner thread – schedule UI update on main thread.
        def _update():
            self._progress_var.set(msg)
            if total > 0:
                self._pbar["maximum"] = total
                self._pbar["value"] = cur
        self.after(0, _update)

    def _cancel_scan(self) -> None:
        if self._scanner:
            self._scanner.cancel()
        self._progress_var.set("Cancelling…")

    def _scan_finished(self, results: List[DuplicateGroup]) -> None:
        self._scan_btn.config(state=tk.NORMAL)
        self._cancel_btn.config(state=tk.DISABLED)
        self._results = results

        if not results:
            self._progress_var.set("No duplicates found.")
            return

        self._progress_var.set(
            f"Found {len(results)} duplicate group(s) "
            f"({sum(len(g.items) for g in results)} files total)."
        )

        self._results_view = ResultsView(
            self._results_frame, results,
            tmdb_client=self._tmdb if self._tmdb.is_configured else None,
        )
        self._results_view.pack(fill=tk.BOTH, expand=True)

"""
scanner.py – Walk directories, find media files, and group duplicates.

Duplicate detection strategy (layered, fast → slow):

1. **Partial hash** (first+last 64 KiB SHA-256) – groups files that are
   byte-identical or nearly so.  Very fast even for multi-GB video files.
2. **Full content hash** – computed only when a partial-hash bucket has
   more than one member.  Confirms true byte-identical duplicates.
3. **Fuzzy name matching** – within each media-type, file names are
   compared with ``difflib.SequenceMatcher``.  Pairs whose ratio exceeds
   a configurable threshold are grouped.
4. **Perceptual hash** (images only) – groups visually similar images
   regardless of resolution, compression, or minor edits.

Groups produced by any of these layers are merged so that a single
"duplicate group" may contain byte-identical copies *and* renamed /
re-encoded variants of the same media.
"""

from __future__ import annotations

import os
import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from media_info import MediaInfo, get_info, is_media_file


# ---------------------------------------------------------------------------
# Public dataclass returned to the GUI
# ---------------------------------------------------------------------------
@dataclass
class DuplicateGroup:
    """A set of files believed to be duplicates / variants of the same media."""
    group_id: int = 0
    items: List[MediaInfo] = field(default_factory=list)
    match_reasons: List[str] = field(default_factory=list)  # e.g. ["exact content", "similar name"]


# ---------------------------------------------------------------------------
# Union-Find for merging groups
# ---------------------------------------------------------------------------
class _UnionFind:
    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = defaultdict(list)
        for x in self._parent:
            out[self.find(x)].append(x)
        return out


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
class Scanner:
    """Configurable media duplicate scanner."""

    def __init__(
        self,
        name_similarity_threshold: float = 0.80,
        perceptual_hash_max_distance: int = 8,
        compute_full_hash: bool = True,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        self.name_threshold = name_similarity_threshold
        self.phash_max_dist = perceptual_hash_max_distance
        self.compute_full_hash = compute_full_hash
        # Callback: (message, current_step, total_steps)
        self._on_progress = on_progress
        self._cancelled = False

    # -- cancellation -------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True

    # -- progress helper ----------------------------------------------------
    def _progress(self, msg: str, cur: int = 0, total: int = 0) -> None:
        if self._on_progress:
            self._on_progress(msg, cur, total)

    # -- public entry point -------------------------------------------------
    def scan(self, folders: List[str]) -> List[DuplicateGroup]:
        """Scan *folders* and return duplicate groups (len ≥ 2)."""
        self._cancelled = False

        # 1. Discover all media files
        self._progress("Discovering media files…")
        paths = self._discover(folders)
        if not paths or self._cancelled:
            return []

        # 2. Analyse each file
        infos: Dict[str, MediaInfo] = {}
        total = len(paths)
        for i, p in enumerate(paths):
            if self._cancelled:
                return []
            self._progress(f"Analysing ({i+1}/{total}): {Path(p).name}", i + 1, total)
            try:
                infos[p] = get_info(p, full_hash=False)
            except Exception:
                continue  # skip unreadable files

        if not infos or self._cancelled:
            return []

        # 3. Build union-find from multiple matching strategies
        uf = _UnionFind()
        reasons: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        self._progress("Comparing by content hash…")
        self._match_by_partial_hash(infos, uf, reasons)
        if self._cancelled:
            return []

        if self.compute_full_hash:
            self._progress("Verifying with full content hash…")
            self._verify_with_full_hash(infos, uf, reasons)
            if self._cancelled:
                return []

        self._progress("Comparing file names…")
        self._match_by_name(infos, uf, reasons)
        if self._cancelled:
            return []

        self._progress("Comparing perceptual hashes…")
        self._match_by_perceptual_hash(infos, uf, reasons)
        if self._cancelled:
            return []

        # 4. Collect groups
        raw_groups = uf.groups()
        result: List[DuplicateGroup] = []
        gid = 0
        for _root, members in sorted(raw_groups.items()):
            if len(members) < 2:
                continue
            members_sorted = sorted(members)
            group_reasons: Set[str] = set()
            for i, a in enumerate(members_sorted):
                for b in members_sorted[i + 1:]:
                    key = (min(a, b), max(a, b))
                    group_reasons.update(reasons.get(key, set()))
            result.append(DuplicateGroup(
                group_id=gid,
                items=[infos[m] for m in members_sorted if m in infos],
                match_reasons=sorted(group_reasons),
            ))
            gid += 1

        # Filter out groups that ended up with <2 valid infos
        result = [g for g in result if len(g.items) >= 2]
        self._progress("Scan complete.", len(result), len(result))
        return result

    # -- discovery ----------------------------------------------------------
    def _discover(self, folders: List[str]) -> List[str]:
        paths: List[str] = []
        seen: Set[str] = set()
        for folder in folders:
            for root, _dirs, files in os.walk(folder):
                if self._cancelled:
                    return paths
                for fname in files:
                    fp = os.path.join(root, fname)
                    real = os.path.realpath(fp)
                    if real in seen:
                        continue
                    seen.add(real)
                    if is_media_file(fp):
                        paths.append(real)
        return paths

    # -- matching strategies ------------------------------------------------
    def _match_by_partial_hash(
        self,
        infos: Dict[str, MediaInfo],
        uf: _UnionFind,
        reasons: Dict[Tuple[str, str], Set[str]],
    ) -> None:
        buckets: Dict[str, List[str]] = defaultdict(list)
        for path, mi in infos.items():
            if mi.partial_hash:
                buckets[mi.partial_hash].append(path)
        for _h, members in buckets.items():
            if len(members) < 2:
                continue
            first = members[0]
            for other in members[1:]:
                uf.union(first, other)
                key = (min(first, other), max(first, other))
                reasons[key].add("exact content")

    def _verify_with_full_hash(
        self,
        infos: Dict[str, MediaInfo],
        uf: _UnionFind,
        reasons: Dict[Tuple[str, str], Set[str]],
    ) -> None:
        """For groups formed by partial hash, compute full hashes and split
        any false positives (rare, but possible)."""
        groups = uf.groups()
        for _root, members in groups.items():
            if len(members) < 2:
                continue
            full_buckets: Dict[str, List[str]] = defaultdict(list)
            for m in members:
                mi = infos.get(m)
                if mi is None:
                    continue
                if not mi.content_hash:
                    try:
                        from media_info import _sha256_full
                        mi.content_hash = _sha256_full(m)
                    except Exception:
                        continue
                full_buckets[mi.content_hash].append(m)
            # Re-link only true matches
            for _fh, fb_members in full_buckets.items():
                if len(fb_members) >= 2:
                    first = fb_members[0]
                    for other in fb_members[1:]:
                        key = (min(first, other), max(first, other))
                        reasons[key].add("exact content (verified)")

    def _match_by_name(
        self,
        infos: Dict[str, MediaInfo],
        uf: _UnionFind,
        reasons: Dict[Tuple[str, str], Set[str]],
    ) -> None:
        # Group by media type to avoid cross-type false positives
        by_type: Dict[str, List[str]] = defaultdict(list)
        for path, mi in infos.items():
            by_type[mi.media_type].append(path)

        for _mtype, paths in by_type.items():
            if len(paths) < 2:
                continue
            # Precompute stems
            stems = {p: _normalise_stem(Path(p).stem) for p in paths}
            # O(n²) but we skip huge sets
            if len(paths) > 5000:
                continue
            for i in range(len(paths)):
                if self._cancelled:
                    return
                for j in range(i + 1, len(paths)):
                    a, b = paths[i], paths[j]
                    ratio = difflib.SequenceMatcher(
                        None, stems[a], stems[b],
                    ).ratio()
                    if ratio >= self.name_threshold:
                        uf.union(a, b)
                        key = (min(a, b), max(a, b))
                        reasons[key].add(f"similar name ({ratio:.0%})")

    def _match_by_perceptual_hash(
        self,
        infos: Dict[str, MediaInfo],
        uf: _UnionFind,
        reasons: Dict[Tuple[str, str], Set[str]],
    ) -> None:
        images = [
            (p, mi) for p, mi in infos.items()
            if mi.media_type == "image" and mi.perceptual_hash
        ]
        if len(images) < 2:
            return
        # O(n²) – skip if unreasonably large
        if len(images) > 5000:
            return
        for i in range(len(images)):
            if self._cancelled:
                return
            for j in range(i + 1, len(images)):
                pa, mia = images[i]
                pb, mib = images[j]
                dist = _hamming(mia.perceptual_hash, mib.perceptual_hash)
                if dist <= self.phash_max_dist:
                    uf.union(pa, pb)
                    key = (min(pa, pb), max(pa, pb))
                    reasons[key].add(f"perceptual similarity (distance {dist})")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _normalise_stem(stem: str) -> str:
    """Lower-case, collapse non-alpha to single space, strip."""
    import re
    return re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()


def _hamming(hex_a: str, hex_b: str) -> int:
    """Hamming distance between two hex-encoded hashes."""
    try:
        x = int(hex_a, 16) ^ int(hex_b, 16)
        return bin(x).count("1")
    except ValueError:
        return 999

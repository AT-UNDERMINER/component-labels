"""
core/datasheet.py — datasheet PDF download.

Refactored from the `component_lookup.py` prototype. Owns everything that
touches datasheet PDFs:

    datasheet_path_for()    canonical cached-PDF path for an MPN
    download_pdf()          fetch a datasheet (cache-aware, atomic write)

The datasheet is downloaded purely to cache a local copy (linked from the web
UI's detail page); nothing is extracted from it. Pinout/package diagrams come
from the bundled package-outline SVG library instead — see
core/label_builder.resolve_package_image(). The old keyword-scoring pinout
extractor that used to live here has been removed.

All output locations come from config.py (DATASHEET_DIR) — nothing is
hardcoded. The run() orchestrator was deliberately NOT moved here; that wiring
belongs to main.py.

Failure model: download_pdf() returns None on failure (network problems are
expected and non-fatal).
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

import config


# ── Datasheet download ────────────────────────────────────────────────────────

def _safe_filename(mpn: str) -> str:
    """Filesystem-safe filename stem for an MPN (e.g. 'BC547B/TR' -> 'BC547B_TR')."""
    return re.sub(r"[^\w\-]", "_", mpn)


def datasheet_path_for(mpn: str) -> Path:
    """Canonical local path for a part's cached datasheet PDF."""
    return config.DATASHEET_DIR / f"{_safe_filename(mpn)}.pdf"


def download_pdf(
    url: str,
    mpn: str,
    *,
    force: bool = False,
    dest_path: Path | None = None,
) -> Path | None:
    """Fetch a datasheet PDF, or reuse the cached copy if one exists.

    The destination defaults to DATASHEET_DIR/<safe-mpn>.pdf (pass dest_path
    to override). If the file already exists it is returned immediately
    without any network traffic, unless force=True.

    The response is validated to actually be a PDF (magic bytes) and written
    atomically (tmp file + rename), so a failed/interrupted download can never
    leave a half-written file behind to be mistaken for a cached datasheet.

    Returns the local path on success, None on failure.
    """
    dest = dest_path if dest_path is not None else datasheet_path_for(mpn)

    if dest.exists() and not force:
        print(f"  Datasheet already cached -> {dest}")
        return dest

    headers = {"User-Agent": "Mozilla/5.0 (component-label-system)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [!] Could not download PDF: {exc}")
        return None

    content = resp.content
    # Reject HTML error pages / redirect stubs masquerading as datasheets:
    # a real PDF starts with %PDF (allow a little leading junk, which some
    # publishers emit).
    if b"%PDF" not in content[:1024]:
        print(f"  [!] URL did not return a PDF (got {len(content)} bytes "
              f"starting {content[:12]!r})")
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".pdf.part")
    try:
        tmp.write_bytes(content)
        tmp.replace(dest)  # atomic on the same filesystem
    except OSError as exc:
        print(f"  [!] Could not write PDF to disk: {exc}")
        tmp.unlink(missing_ok=True)
        return None

    print(f"  Downloaded datasheet -> {dest}")
    return dest

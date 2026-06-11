"""
core/datasheet.py — datasheet PDF download + pinout page extraction.

Refactored from the `component_lookup.py` prototype. Owns everything that
touches datasheet PDFs:

    datasheet_path_for()    canonical cached-PDF path for an MPN
    download_pdf()          fetch a datasheet (cache-aware, atomic write)
    score_page()            heuristic "is this a pinout page?" score
    extract_pinout_pages()  rasterize the top-N candidate pages to PNG

All output locations come from config.py (DATASHEET_DIR, IMAGE_DIR) — nothing
is hardcoded. The run() orchestrator was deliberately NOT moved here; that
wiring belongs to main.py.

Failure model: download_pdf() returns None on failure (network problems are
expected and non-fatal); extract_pinout_pages() lets fitz errors propagate
(a corrupt PDF reaching it is a bug upstream — download_pdf validates the
magic bytes precisely so that can't happen via the normal pipeline).
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF
import requests

import config


# ── Scoring constants ─────────────────────────────────────────────────────────

# Keywords used to score pages likely to contain a pinout diagram.
PINOUT_KEYWORDS = [
    "pin", "pinout", "pin out", "pin description", "pin configuration",
    "pin assignment", "pin function", "terminal", "package", "connections",
    "diagram", "schematic",
]

# Keywords weighted double in the score — the strongest pinout signals.
_STRONG_KEYWORDS = ("pin", "pinout", "diagram")

_IMAGE_WEIGHT = 1.5   # score contribution per embedded raster image
_RASTER_ZOOM = 2.0    # 2× zoom ≈ 144 DPI — good quality for a 45mm label


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


# ── Pinout page detection & extraction ────────────────────────────────────────

def _page_stats(page: fitz.Page) -> tuple[float, int]:
    """Return (keyword_score, embedded_raster_image_count) for one page."""
    text = page.get_text().lower()

    kw_score = 0.0
    for kw in PINOUT_KEYWORDS:
        kw_score += text.count(kw) * (2.0 if kw in _STRONG_KEYWORDS else 1.0)

    return kw_score, len(page.get_images())


def score_page(page: fitz.Page, count_images: bool = True) -> float:
    """Heuristic score for how likely a page contains a pinout/package diagram.

    Higher = more likely. Keyword hits are weighted (strong pinout terms ×2),
    and each embedded raster image adds _IMAGE_WEIGHT — unless
    count_images=False, which scores on keyword density alone (used for
    vector-only datasheets where no page has any raster image).
    """
    kw_score, image_count = _page_stats(page)
    if count_images:
        return kw_score + image_count * _IMAGE_WEIGHT
    return kw_score


def _rank_candidate_pages(doc: fitz.Document, top_n: int) -> list[tuple[int, float]]:
    """Rank a document's pages by pinout likelihood as (page_index, score),
    best first, keeping at most top_n. Zero-scored pages are excluded.

    Implements the vector-pinout fallback: if no page in the whole document
    has an embedded raster image, ranking uses keyword density alone.
    """
    stats = [(i, *_page_stats(doc[i])) for i in range(len(doc))]

    # Known gap (see CLAUDE.md): vector-drawn pinouts embed no raster
    # images, so the image term is useless — score by keywords only.
    if sum(img_count for _, _, img_count in stats) == 0:
        print("  [i] No embedded raster images in this PDF (vector-drawn "
              "datasheet?) — ranking pages by keyword density only.")
        scores = [(i, kw) for i, kw, _ in stats]
    else:
        scores = [(i, kw + img * _IMAGE_WEIGHT) for i, kw, img in stats]

    # Drop pages with nothing pinout-like on them at all, then rank.
    candidates = [(i, s) for i, s in scores if s > 0]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def extract_pinout_pages(
    pdf_path: Path,
    mpn: str | None = None,
    *,
    output_dir: Path | None = None,
    top_n: int = 3,
) -> list[Path]:
    """Score every page in the PDF and rasterize the top_n most likely
    pinout/diagram pages to PNG.

    Output goes to IMAGE_DIR/<mpn>/ by default (per the project layout), with
    files named pinout_candidate_<rank>_p<page>.png so rank 1 is the default
    pinout image and the source page stays traceable. Pass output_dir to
    override the location entirely.

    Vector-pinout fallback: if NO page in the document contains an embedded
    raster image (typical of vector-drawn datasheets), pages are ranked by
    keyword density alone. Pages scoring zero are never returned — fewer than
    top_n candidates (or none at all, with a warning) beats rasterizing pages
    that have nothing to do with pinouts.

    Returns the saved image paths, best candidate first.
    """
    if output_dir is None:
        stem = mpn if mpn else pdf_path.stem
        output_dir = config.IMAGE_DIR / _safe_filename(stem)

    saved: list[Path] = []

    with fitz.open(str(pdf_path)) as doc:
        top_pages = _rank_candidate_pages(doc, top_n)

        if not top_pages:
            print(f"  [!] No pinout candidates found in {pdf_path.name} — "
                  "every page scored zero. Use a manual image override.")
            return []

        output_dir.mkdir(parents=True, exist_ok=True)

        for rank, (page_idx, page_score) in enumerate(top_pages, start=1):
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(_RASTER_ZOOM, _RASTER_ZOOM))

            out_path = output_dir / f"pinout_candidate_{rank}_p{page_idx + 1}.png"
            pix.save(str(out_path))
            saved.append(out_path)

            print(f"  Page {page_idx + 1:>3}  score={page_score:6.1f}  -> {out_path.name}")

    return saved

"""
core/pdf_renderer.py — render cached labels onto print-ready A4 sheets (step 7).

    render_sheet(mpns, output_path, *, start=1) -> Path   ← public entry point

Pipeline position: the final step. Given an ordered list of cached MPNs it
builds each label's HTML via core/label_builder.build_label(), lays the labels
out on an A4 grid using the geometry constants in config.py, and rasterises the
whole job to a single multi-page PDF with WeasyPrint.

Layout model
------------
The sheet is one absolutely-positioned A4 page per `.sheet` div. Each label is
dropped into a 45x45mm `.cell` placed at the millimetre coordinate computed
from config's margins / gaps / label size. Positions are numbered 1..N
left-to-right then top-to-bottom (1 = top-left). `start` is the first position
filled, so a part-used sheet isn't wasted; when a sheet fills, the job spills
onto a fresh page (positions 1..N).

Why we only keep each label's <body>
------------------------------------
build_label() returns a *complete* single-label HTML document (its own <head>,
its own `@page { size: 45mm 45mm }`). We splice out just the inner `.label`
markup and drop the per-label page box — the sheet supplies its own A4 @page.
The shared stylesheet and the labels' image URLs are written relative to
templates/, so WeasyPrint is given base_url=config.TEMPLATE_DIR to resolve them
(identical to how label_builder documents its output).

WeasyPrint is a heavy, platform-specific dependency (trivial on the Linux
target, awkward on Windows). It is imported lazily inside render_sheet() so
this module — and anything that imports it (main.py, app.py) — loads fine
without it; only an actual render needs it, and its absence raises a clear
RuntimeError instead of an ImportError at module load.
"""

from __future__ import annotations

import re
from pathlib import Path

import config
from core import cache, label_builder


# Height of the top colour bar inside every label, in mm. Mirrors
# `.type-bar { height: 6.5mm }` in templates/label_styles.css — the bleed
# underlay paints the bar colour this far down (plus the bleed) so the colour
# reaches the top edge behind the (safe-zone-inset) label.
_BAR_HEIGHT_MM = 6.5


# ── Print-finishing geometry (bleed + safe zone) ───────────────────────────────

def _safe_scale() -> float:
    """Uniform scale that fits the 45mm artwork inside the safe zone.

    config.SAFE_ZONE_MM is how far content is kept inside the trim on every
    side, so the label is scaled to (W - 2·safe)/W and centred. Clamped to a
    sane floor so an over-large safe zone can't scale the label to nothing.
    """
    w = config.LABEL_WIDTH_MM
    safe = config.SAFE_ZONE_MM
    return max(0.1, (w - 2.0 * safe) / w) if w else 1.0


# ── Grid geometry ──────────────────────────────────────────────────────────────

def _cell_xy(position: int) -> tuple[float, float]:
    """Top-left (left_mm, top_mm) of label `position` (1-based) on a sheet."""
    idx = position - 1
    row, col = divmod(idx, config.GRID_COLS)
    left = config.MARGIN_LEFT_MM + col * (config.LABEL_WIDTH_MM + config.GAP_H_MM)
    top = config.MARGIN_TOP_MM + row * (config.LABEL_HEIGHT_MM + config.GAP_V_MM)
    return left, top


def _paginate(labels: list[dict], start: int) -> list[list[dict]]:
    """Group placed labels into sheets, each a list of positioned-cell dicts.

    Each input label is a dict carrying at least {"html", "colour"}; the output
    copies it with {position, left, top} added. Returns one inner list per A4
    page. The first label lands at `start`; each page holds positions up to
    config.LABELS_PER_SHEET before the job spills onto a new page.
    """
    per_sheet = config.LABELS_PER_SHEET
    sheets: list[list[dict]] = [[]]
    position = start
    for label in labels:
        if position > per_sheet:
            sheets.append([])
            position = 1
        left, top = _cell_xy(position)
        sheets[-1].append({**label, "position": position, "left": left, "top": top})
        position += 1
    return sheets


# ── HTML assembly (pure — no WeasyPrint needed, so it is unit-testable) ─────────

_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)


def _label_fragment(full_html: str) -> str:
    """Extract the inner `.label` markup from a build_label() document.

    build_label() emits a whole HTML page; on the sheet we want only what's
    inside <body>. Falls back to the full string if the body can't be located
    (shouldn't happen with our templates, but never silently drops a label).
    """
    match = _BODY_RE.search(full_html)
    return match.group(1).strip() if match else full_html


def _cell_html(cell: dict) -> str:
    """One placed label cell: a bleed underlay behind the label fragment.

    The underlay carries the label's colour-bar colour so that — once the label
    itself is inset by the safe zone (via the --safe-scale transform in
    label_styles.css) — the colour still runs to the top/edge of the trim and
    the surrounding quiet zone stays white. The cell clips at the trim, so the
    underlay's `bleed` overhang guarantees full edge coverage.

    `cell` carries the pre-built label HTML and its bleed colour, so this is
    agnostic to whether the label came from a cached MPN or a generic group.
    """
    colour = cell.get("colour") or config.type_colour(None)
    fragment = _label_fragment(cell["html"])
    return (
        f'    <div class="cell" style="left: {cell["left"]:.3f}mm; top: {cell["top"]:.3f}mm;">\n'
        f'      <div class="bleed-underlay" style="--bar-colour: {colour};"></div>\n'
        f"{fragment}\n"
        f"    </div>"
    )


def _labels_from_mpns(mpns: list[str]) -> list[dict]:
    """Build the {"html", "colour"} cell dicts for a list of cached MPNs.

    Each MPN's label HTML comes from label_builder.build_label() and its bleed
    colour from the cached component's type — so the MPN-based entry points share
    one definition of "a label for an MPN" with the generic path.
    """
    labels: list[dict] = []
    for mpn in mpns:
        colour = config.type_colour(
            (cache.get_component(mpn) or {}).get("component_type")
        )
        labels.append({"html": label_builder.build_label(mpn), "colour": colour})
    return labels


def build_sheet_html(mpns: list[str], *, start: int = 1) -> str:
    """Compose the full multi-page A4 HTML for a print job of cached MPNs.

    Thin wrapper over build_sheet_html_from_labels(): resolves each MPN to its
    label HTML + colour, then lays them out. Separated from render_sheet() so
    the layout can be built and tested without WeasyPrint installed.
    """
    if not mpns:
        raise ValueError("build_sheet_html: no MPNs given.")
    return build_sheet_html_from_labels(_labels_from_mpns(mpns), start=start)


def build_sheet_html_from_labels(labels: list[dict], *, start: int = 1) -> str:
    """Compose the full multi-page A4 HTML from pre-built label cells.

    `labels` is an ordered list of {"html", "colour"} dicts — "html" is a
    complete single-label document (from build_label() or build_generic_label()),
    "colour" is its bleed-underlay colour. This is the MPN-agnostic core shared
    by cached-part jobs (build_sheet_html) and generic-group jobs.
    """
    if not labels:
        raise ValueError("build_sheet_html_from_labels: no labels given.")
    per_sheet = config.LABELS_PER_SHEET
    if not (1 <= start <= per_sheet):
        raise ValueError(f"start must be between 1 and {per_sheet} (got {start}).")

    sheets = _paginate(labels, start)

    sheet_blocks: list[str] = []
    for sheet in sheets:
        cells = "\n".join(_cell_html(c) for c in sheet)
        sheet_blocks.append(f'  <div class="sheet">\n{cells}\n  </div>')

    body = "\n".join(sheet_blocks)

    bleed = config.BLEED_MM
    safe = config.SAFE_ZONE_MM
    safe_scale = _safe_scale()
    bar_strip = _BAR_HEIGHT_MM + bleed  # underlay colour height (bar + bleed)

    # Stylesheet href + label image URLs are relative to templates/ — the
    # caller renders with base_url=config.TEMPLATE_DIR so they resolve.
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="label_styles.css">
  <style>
    @page {{ size: {config.SHEET_WIDTH_MM}mm {config.SHEET_HEIGHT_MM}mm; margin: 0; }}
    html, body {{ margin: 0; padding: 0; }}
    .sheet {{
      position: relative;
      width: {config.SHEET_WIDTH_MM}mm;
      height: {config.SHEET_HEIGHT_MM}mm;
      overflow: hidden;
      page-break-after: always;
    }}
    .sheet:last-child {{ page-break-after: auto; }}
    .cell {{
      position: absolute;
      width: {config.LABEL_WIDTH_MM}mm;
      height: {config.LABEL_HEIGHT_MM}mm;
      overflow: hidden;
      /* Safe zone: --safe-scale shrinks the label into the safe area (consumed
         by .label in label_styles.css); --safe-zone is the same inset in mm,
         exposed for the templates to reference. */
      --safe-zone: {safe}mm;
      --safe-scale: {safe_scale:.5f};
    }}
    /* The label paints above its bleed underlay. */
    .cell > .label {{ position: relative; z-index: 1; }}
    /* Bleed: a colour/white underlay that extends {bleed}mm past the trim on
       every side, so the colour bar reaches the edge even after the label is
       inset by the safe zone (and even if the printer is a hair off). */
    .bleed-underlay {{
      position: absolute;
      inset: -{bleed}mm;
      z-index: 0;
      background: #fff;
    }}
    .bleed-underlay::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: {bar_strip:.3f}mm;
      background: var(--bar-colour, transparent);
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


# ── Public API ──────────────────────────────────────────────────────────────────

def render_labels(
    labels: list[dict],
    output_path: str | Path,
    *,
    start: int = 1,
) -> Path:
    """Render pre-built label cells to a print-ready A4 label PDF.

    `labels` is an ordered list of {"html", "colour"} dicts (see
    build_sheet_html_from_labels). This is the MPN-agnostic renderer behind both
    cached-part jobs (render_sheet) and on-the-fly generic-group jobs (the web
    UI's generic print flow). Writes one multi-page PDF and returns its path.

    Raises ValueError for bad input (no labels, out-of-range start) and
    RuntimeError if WeasyPrint is not installed.
    """
    html = build_sheet_html_from_labels(labels, start=start)

    # Lazy import: keep the heavy/platform-specific dependency out of module load.
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is required to render label PDFs but is not installed. "
            "On the Linux target: `pip install weasyprint` (plus its system "
            "libraries). See CLAUDE.md > Dependencies."
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # base_url = templates/ so the stylesheet and the labels' ../output/... image
    # URLs resolve to real files on disk.
    HTML(string=html, base_url=str(config.TEMPLATE_DIR)).write_pdf(str(output_path))
    return output_path


def render_sheet(
    mpns: list[str],
    output_path: str | Path,
    *,
    start: int = 1,
) -> Path:
    """Render an ordered list of cached MPNs to a print-ready A4 label PDF.

    Labels fill positions left-to-right, top-to-bottom starting at `start`
    (1..config.LABELS_PER_SHEET); the job spills onto extra pages as needed.
    Writes one multi-page PDF to output_path and returns it.

    Raises ValueError for bad input (no MPNs, out-of-range start), LookupError
    if an MPN is not cached (propagated from build_label), and RuntimeError if
    WeasyPrint is not installed.
    """
    if not mpns:
        raise ValueError("render_sheet: no MPNs given.")
    return render_labels(_labels_from_mpns(mpns), output_path, start=start)

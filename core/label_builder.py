"""
core/label_builder.py — turns a cached component record into label HTML.

    build_label(mpn, overrides=None) -> str   ← the public entry point

Pipeline position: step 6 of the data pipeline (after cache write). Reads
the component record from core/cache.py, picks the right template via
config.type_template(), extracts the per-type key specs defined in
config.LABEL_SPECS, generates the QR code, and renders the Jinja2 template
to an HTML string ready for core/pdf_renderer.py (or the labels table).

Path convention: image URLs in the rendered HTML are written relative to
the templates/ directory (e.g. "../output/images/NE555/qr.png"), matching
the stylesheet <link> inside the templates. Whatever renders this HTML —
WeasyPrint or a browser — must therefore resolve URLs against templates/
(pdf_renderer will pass base_url=config.TEMPLATE_DIR).

QR codes link to the component's page on the local web UI:
http://<config.QR_HOST>/component/<mpn>. If the qrcode library is missing
the label simply renders without a QR code (a warning is printed).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

import config
from core import cache
from core.datasheet import _safe_filename

try:
    import qrcode  # optional at import time; required for QR codes on labels
except ImportError:  # pragma: no cover - exercised only without qrcode[pil]
    qrcode = None


_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATE_DIR)),
    autoescape=select_autoescape(("html",)),
    undefined=StrictUndefined,  # template/context drift fails loudly, not silently
)


# ── Bar text colour (relative luminance) ──────────────────────────────────────

def _srgb_channel(value8: int) -> float:
    """Linearise one 8-bit sRGB channel (per the WCAG definition)."""
    c = value8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance (0 = black, 1 = white) of a #RRGGBB colour."""
    h = hex_colour.lstrip("#")
    if len(h) == 3:  # allow shorthand like #fa0
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _srgb_channel(r)
        + 0.7152 * _srgb_channel(g)
        + 0.0722 * _srgb_channel(b)
    )


def bar_text_colour(background_hex: str) -> str:
    """Black or white — whichever has the higher WCAG contrast ratio
    against the given background.

    Black wins when L > 0.179: the crossover point where
    (L + 0.05) / 0.05  >  1.05 / (L + 0.05).
    """
    return "#000000" if relative_luminance(background_hex) > 0.179 else "#ffffff"


# ── Spec extraction ───────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Normalise for matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _spec_value(raw_specs: list, match_terms: list[str]) -> str | None:
    """First displayValue whose attribute matches any of match_terms.

    raw_specs is the Nexar-shaped list stored in the cache:
    [{"attribute": {"name": ..., "shortname": ...}, "displayValue": ...}].
    Terms (already normalised in config) are substring-matched against the
    normalised shortname and name; the "|" separator stops a term from
    accidentally spanning the boundary between the two.
    """
    for spec in raw_specs:
        attr = spec.get("attribute") or {}
        haystack = (
            _norm(attr.get("shortname") or "") + "|" + _norm(attr.get("name") or "")
        )
        for term in match_terms:
            if term in haystack:
                value = spec.get("displayValue")
                if value:
                    return str(value)
    return None


def _key_specs(
    raw_specs: list,
    wanted: list[tuple[str, list[str]]],
    limit: int = 3,
) -> list[dict[str, str]]:
    """Resolve config.LABEL_SPECS entries against a component's raw specs.

    Returns up to `limit` {"name", "value"} dicts in the configured order;
    missing attributes are skipped, so later entries fill vacated slots.
    """
    found: list[dict[str, str]] = []
    for label, terms in wanted:
        value = _spec_value(raw_specs, terms)
        if value:
            found.append({"name": label, "value": value})
        if len(found) == limit:
            break
    return found


# ── QR code ───────────────────────────────────────────────────────────────────

def _generate_qr(mpn: str) -> Path | None:
    """Write the QR PNG for a component's web-UI page; return its path.

    Regenerated on every build (it is cheap, and QR_HOST may have changed
    since the last build). Returns None if the qrcode library is missing.
    """
    if qrcode is None:
        print("  [!] qrcode library not installed — label will have no QR code.")
        return None

    url = f"http://{config.QR_HOST}/component/{mpn}"
    out_dir = config.IMAGE_DIR / _safe_filename(mpn)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "qr.png"

    # border=2 modules (not the default 4): the label supplies its own white
    # space, and a thinner quiet zone buys bigger modules at 7mm print size.
    qrcode.make(url, border=2).save(str(out_path))
    return out_path


# ── Path → template-relative URI ──────────────────────────────────────────────

def _template_uri(path: str | Path | None) -> str | None:
    """Convert a stored (project-root-relative) path to a URI that resolves
    from the templates/ directory, e.g. "../output/images/NE555/qr.png".

    Absolute paths inside the project are relativised first; absolute paths
    outside it are passed through as-is. None stays None (template slots
    collapse via their {% if %} guards).
    """
    if not path:
        return None
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(config.PROJECT_ROOT)
        except ValueError:
            return p.as_posix()  # outside the project tree — leave alone
    return "../" + p.as_posix()


# ── Public API ────────────────────────────────────────────────────────────────

def build_label(mpn: str, overrides: dict[str, Any] | None = None) -> str:
    """Render the print-ready label HTML for a cached component.

    The template is chosen from the component's type via
    config.type_template(). The context is a superset of every variable any
    template uses, so an override that switches templates always works.

    overrides — optional dict from the web UI's edit page, applied without
    touching the DB. The special key "template" swaps the template file
    (e.g. "label_complex.html"); every other key replaces the context
    variable of the same name (value, specs, pinout_image, ...). Image
    overrides must be URIs resolvable from the templates/ directory.

    Raises LookupError if the MPN is not in the cache.
    """
    overrides = dict(overrides or {})
    record = cache.get_component(mpn)
    if record is None:
        raise LookupError(
            f"'{mpn}' is not in the cache — add it first (main.py add {mpn})"
        )

    ctype = record.get("component_type")
    template_name = overrides.pop("template", None) or config.type_template(ctype)

    raw_specs = record.get("specs") or []
    spec_cfg = config.LABEL_SPECS.get(ctype or "", {})
    colour = config.type_colour(ctype)

    context: dict[str, Any] = {
        "type_colour": colour,
        "bar_text_colour": bar_text_colour(colour),
        "type_name": config.type_display_name(ctype),
        "mpn": record["mpn"],
        "description": record.get("description") or "",
        # Simple label's value line; falls back to the description so a part
        # with unmatchable specs still shows something useful.
        "value": _spec_value(raw_specs, spec_cfg.get("value", []))
        or record.get("description")
        or "",
        "specs": _key_specs(raw_specs, spec_cfg.get("specs", [])),
        # Connector stat boxes; an em dash marks "unknown" visibly.
        "pin_count": _spec_value(raw_specs, spec_cfg.get("pins", [])) or "—",
        "pitch": _spec_value(raw_specs, spec_cfg.get("pitch", [])) or "—",
        "package_image": _template_uri(record.get("package_image_path")),
        "pinout_image": _template_uri(record.get("pinout_image_path")),
        "qr_image": _template_uri(_generate_qr(mpn)),
    }
    context.update(overrides)

    return _env.get_template(template_name).render(context)

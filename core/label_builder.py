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

QR codes link to the component's datasheet URL when one is cached, falling back
to its page on the local web UI (http://<config.QR_HOST>/component/<mpn>) when
no datasheet URL is available. If the qrcode library is missing the label
simply renders without a QR code (a warning is printed).
"""

from __future__ import annotations

import math
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

def _generate_qr(mpn: str, datasheet_url: str | None = None) -> Path | None:
    """Write a component's QR PNG and return its path.

    The QR encodes the component's datasheet URL when one is cached, falling
    back to its page on the local web UI (http://<config.QR_HOST>/component/<mpn>)
    when no datasheet URL is available.

    Regenerated on every build (it is cheap, and the datasheet URL or QR_HOST
    may have changed since the last build). Returns None if the qrcode library
    is missing.
    """
    if qrcode is None:
        print("  [!] qrcode library not installed — label will have no QR code.")
        return None

    url = datasheet_url or f"http://{config.QR_HOST}/component/{mpn}"
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


# ── Package diagram resolution ────────────────────────────────────────────────

def resolve_package_image(part_record: dict[str, Any]) -> Path | None:
    """Map a component's package/case to a bundled outline SVG.

    Reads the package name from the record's specs (e.g. "TO-92-3",
    "SOIC-8", "0805 (2012 Metric)"), normalises it to lowercase
    alphanumerics, and matches it against config.PACKAGE_MAP — trying the
    longest alias first so "sot235" resolves to SOT-23-5 rather than SOT-23.

    Returns the project-root-relative path to the SVG in templates/packages/,
    or None when the package is absent or unrecognised (the caller's image
    slot then collapses as usual). Works on any record following the provider
    contract's specs shape, so it suits both cached records and raw lookups.
    """
    pkg = _spec_value(
        part_record.get("specs") or [],
        ["supplierdevicepackage", "package", "case"],
    )
    if not pkg:
        return None

    haystack = _norm(pkg)
    for alias in sorted(config.PACKAGE_MAP, key=lambda a: len(_norm(a)), reverse=True):
        if _norm(alias) and _norm(alias) in haystack:
            path = config.PACKAGE_DIR / config.PACKAGE_MAP[alias]
            try:
                return path.relative_to(config.PROJECT_ROOT)
            except ValueError:  # PACKAGE_DIR moved outside the project tree
                return path
    return None


def package_review_status(part_record: dict[str, Any]) -> tuple[str, str | None]:
    """Decide a component's label status from package-diagram resolution.

    Package outlines are the only automatic source of pinout/package diagrams
    now (datasheet extraction has been removed), so a part is print-ready iff
    its package resolves to a bundled SVG. Returns a (status, review_reason)
    pair:

      ("ready", None)
          resolve_package_image() matched a bundled outline SVG.
      ("needs_review", "No package diagram found for '<package>'")
          the provider gave a package/case spec, but no SVG matches it — a new
          outline should be drawn (or the part edited + approved by hand).
      ("needs_review", "Package not identified")
          the provider returned no package/case spec at all, so there is
          nothing to match against.

    Works on any record following the provider contract's specs shape, so it
    suits both a raw lookup result and a cached record.
    """
    if resolve_package_image(part_record) is not None:
        return "ready", None

    pkg = _spec_value(
        part_record.get("specs") or [],
        ["supplierdevicepackage", "package", "case"],
    )
    if pkg:
        return "needs_review", f"No package diagram found for '{pkg}'"
    return "needs_review", "Package not identified"


# Pin-name <text> elements in the package SVGs carry class="pin-name" so the
# same outline can be relabelled for different parts (e.g. a TO-92 used as a
# BJT "C B E" or a MOSFET "D G S") by a simple text substitution.
_PIN_NAME_RE = re.compile(
    r'(<text\b[^>]*\bclass="[^"]*\bpin-name\b[^"]*"[^>]*>)(.*?)(</text>)',
    re.DOTALL,
)


def substitute_pin_names(svg: str, names: list[str]) -> str:
    """Replace the package SVG's pin-name labels, in document (pin) order.

    Each successive class="pin-name" text element takes the next entry from
    `names`; surplus elements keep their default label, surplus names are
    ignored. Returns the SVG unchanged when `names` is falsy.
    """
    if not names:
        return svg
    it = iter(names)

    def _swap(match: "re.Match[str]") -> str:
        try:
            return match.group(1) + next(it) + match.group(3)
        except StopIteration:
            return match.group(0)

    return _PIN_NAME_RE.sub(_swap, svg)


def _package_diagram(record: dict[str, Any], ctype: str | None, mpn: str) -> Path | None:
    """Resolve a part's package outline SVG, relabelling its pin names by
    component type when config.PACKAGE_PIN_NAMES has a matching entry.

    With no relabelling the shared template SVG is used as-is (default
    labels). When pin names apply, the SVG is read, relabelled via
    substitute_pin_names(), and written to a per-part copy at
    output/images/<MPN>/package_diagram.svg whose path is returned — keeping
    resolve_package_image() a pure path lookup and the mutation here. Returns
    None when the part has no recognised package.
    """
    svg_path = resolve_package_image(record)
    if svg_path is None:
        return None

    names = config.PACKAGE_PIN_NAMES.get((ctype or "", svg_path.name))
    if not names:
        return svg_path  # keep the shared SVG's default labels — no per-part copy

    src = svg_path if svg_path.is_absolute() else config.PROJECT_ROOT / svg_path
    relabelled = substitute_pin_names(src.read_text(encoding="utf-8"), names)

    out_dir = config.IMAGE_DIR / _safe_filename(mpn)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "package_diagram.svg"
    out_path.write_text(relabelled, encoding="utf-8")
    try:
        return out_path.relative_to(config.PROJECT_ROOT)
    except ValueError:  # IMAGE_DIR moved outside the project tree
        return out_path


# ── Resistor colour-band diagram ──────────────────────────────────────────────
# EIA resistor colour code. List index = significant digit / multiplier power.
_BAND_DIGIT_COLOURS = [
    "#000000",  # 0 black
    "#8B4513",  # 1 brown
    "#E11414",  # 2 red
    "#F57C00",  # 3 orange
    "#FFE000",  # 4 yellow
    "#2E9E2E",  # 5 green
    "#1457C7",  # 6 blue
    "#8E26A8",  # 7 violet
    "#8A8A8A",  # 8 grey
    "#FFFFFF",  # 9 white
]
_GOLD = "#C9A227"
_SILVER = "#C4C4C4"

# Tolerance percentage → band colour (standard set). Default is gold (±5%).
_TOL_COLOURS = {
    0.05: "#8A8A8A",   # grey
    0.1: "#8E26A8",    # violet
    0.25: "#1457C7",   # blue
    0.5: "#2E9E2E",    # green
    1: "#8B4513",      # brown
    2: "#E11414",      # red
    5: _GOLD,          # gold
    10: _SILVER,       # silver
}

# Case-sensitive so "MOhm" (mega) and "mOhm" (milli) differ.
_SI_MULT = {"k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3, "G": 1e9, "g": 1e9}


def _parse_resistance(value: str | None) -> float | None:
    """Parse a Nexar resistance string (e.g. "10 kOhm", "4.7 MOhm",
    "330 Ohm", "1 Ω") to ohms, or None if it cannot be read."""
    if not value:
        return None
    s = str(value).strip().replace("Ω", "ohm")  # Ω → ohm
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)", s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    mult = 1.0
    if suffix and not suffix.lower().startswith(("ohm", "r")):
        mult = _SI_MULT.get(suffix[0], 1.0)
    return num * mult


def _resistor_bands(ohms: float) -> tuple[list[int], int] | None:
    """Significant digits + multiplier power-of-ten for a resistance. Uses 2
    significant figures (→ 4-band) when that reproduces the value exactly,
    else 3 (→ 5-band). Returns None for non-positive values."""
    if ohms <= 0:
        return None
    for sig in (2, 3):
        exp = math.floor(math.log10(ohms))
        mult_exp = exp - (sig - 1)
        scaled = round(ohms / (10.0 ** mult_exp))
        if scaled >= 10 ** sig:          # rounding bumped the digit count (99.6→100)
            scaled //= 10
            mult_exp += 1
        if math.isclose(scaled * (10.0 ** mult_exp), ohms, rel_tol=1e-9):
            return [int(c) for c in str(scaled)], mult_exp
    # Not exactly representable in 3 figures — return the best 3-figure fit.
    return [int(c) for c in str(scaled)], mult_exp


def _multiplier_colour(mult_exp: int) -> str | None:
    """Band colour for a power-of-ten multiplier, or None if out of range."""
    if mult_exp == -1:
        return _GOLD
    if mult_exp == -2:
        return _SILVER
    if 0 <= mult_exp <= 9:
        return _BAND_DIGIT_COLOURS[mult_exp]
    return None


def _tolerance_colour(tolerance: str | None) -> str:
    """Band colour for a tolerance string (e.g. "±5%"), defaulting to gold."""
    if tolerance:
        m = re.search(r"(\d+(?:\.\d+)?)", str(tolerance))
        if m:
            val = float(m.group(1))
            for pct, colour in _TOL_COLOURS.items():
                if math.isclose(pct, val):
                    return colour
    return _GOLD


def resistor_band_svg(resistance: str | None, tolerance: str | None = None) -> str:
    """Return an inline SVG of a resistor's colour-band code for a resistance
    string (e.g. "10 kOhm"), or "" if the value can't be parsed/encoded.

    The resistor is drawn vertically to fill the simple label's portrait
    image zone, with wide stripes so the code stays legible at 45mm print
    size. 2 significant figures yield a 4-band code, 3 a 5-band code;
    tolerance defaults to a gold (±5%) band. The SVG is self-sizing
    (width/height 100%) for direct embedding in the label HTML.
    """
    ohms = _parse_resistance(resistance)
    if ohms is None:
        return ""
    bands = _resistor_bands(ohms)
    if bands is None:
        return ""
    digits, mult_exp = bands
    mult_colour = _multiplier_colour(mult_exp)
    if mult_colour is None:                       # value outside the colour code
        return ""

    value_colours = [_BAND_DIGIT_COLOURS[d] for d in digits] + [mult_colour]
    tol_colour = _tolerance_colour(tolerance)

    band_h, gap, top = 2.4, 1.0, 9.0

    def _band(y: float, colour: str) -> str:
        return (
            f'<rect x="3" y="{y:.2f}" width="12" height="{band_h}" '
            f'fill="{colour}" stroke="#444" stroke-width="0.18"/>'
        )

    # Value bands grouped near the top; tolerance band set apart near the
    # bottom (the gap is what tells you which end to read from).
    stripes = "".join(
        _band(top + i * (band_h + gap), c) for i, c in enumerate(value_colours)
    )
    stripes += _band(28.0, tol_colour)

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 40" '
        'width="100%" height="100%" preserveAspectRatio="xMidYMid meet">'
        '<line x1="9" y1="0" x2="9" y2="7.5" stroke="#9a9a9a" stroke-width="1.3"/>'
        '<line x1="9" y1="32.5" x2="9" y2="40" stroke="#9a9a9a" stroke-width="1.3"/>'
        '<rect x="3" y="7" width="12" height="26" rx="1.3" fill="#E8D5A0"/>'
        f"{stripes}"
        '<rect x="3" y="7" width="12" height="26" rx="1.3" fill="none" '
        'stroke="#4a4a4a" stroke-width="0.4"/>'
        "</svg>"
    )


def package_image_path(record: dict[str, Any]) -> Path | None:
    """The effective package image for a record: a stored package photo if set,
    otherwise the resolved (and pin-name-relabelled) package outline diagram.
    Returns a project-root-relative Path or None."""
    stored = record.get("package_image_path")
    if stored:
        return Path(stored)
    return _package_diagram(record, record.get("component_type"), record["mpn"])


# ── Public API ────────────────────────────────────────────────────────────────

def label_context(
    mpn: str, overrides: dict[str, Any] | None = None
) -> tuple[dict[str, Any], str]:
    """Build the render context + template name for a cached component.

    Returns (context, template_name). The context is a superset of every
    variable any template uses, so switching templates always works. This is
    the single source of truth shared by build_label() (which renders it) and
    the web editor (which reads headline/subline/specs to prefill its controls).

    Override precedence, lowest to highest:
      1. computed defaults (type, specs, hierarchy, images);
      2. the component's saved editor overrides (record["overrides"], written by
         the visual editor — template / headline / subline / specs);
      3. the `overrides` argument (e.g. a live, unsaved preview from the editor).
    The special key "template" selects the template file; every other key
    replaces the context variable of the same name.

    Raises LookupError if the MPN is not in the cache.
    """
    record = cache.get_component(mpn)
    if record is None:
        raise LookupError(
            f"'{mpn}' is not in the cache — add it first (main.py add {mpn})"
        )

    # Saved overrides form the base; the caller's overrides win over them.
    merged = {**(record.get("overrides") or {}), **(overrides or {})}

    ctype = record.get("component_type")
    template_name = merged.pop("template", None) or config.type_template(ctype)

    raw_specs = record.get("specs") or []
    spec_cfg = config.LABEL_SPECS.get(ctype or "", {})
    colour = config.type_colour(ctype)

    mpn_id = record["mpn"]
    description = record.get("description") or ""
    # The resolved primary value (e.g. "10 kΩ"). Electrolytic/ceramic caps
    # combine capacitance with the voltage rating (config "value2") into one
    # headline like "100 µF 50 V": join with a space when both resolve, use
    # whichever single part is present otherwise, and fall back to the
    # description so a part with unmatchable specs still shows something.
    value_parts = [
        _spec_value(raw_specs, spec_cfg.get("value", [])),
        _spec_value(raw_specs, spec_cfg.get("value2", [])),
    ]
    value = " ".join(p for p in value_parts if p) or description
    specs = _key_specs(raw_specs, spec_cfg.get("specs", []))
    # Connector stat boxes; an em dash marks "unknown" visibly.
    pin_count = _spec_value(raw_specs, spec_cfg.get("pins", [])) or "—"
    pitch = _spec_value(raw_specs, spec_cfg.get("pitch", [])) or "—"

    # ── Information hierarchy ──────────────────────────────────────────────
    # The headline is the label's largest text — the most useful way to
    # identify the part at a glance. For most types that is the value (e.g.
    # "10 kΩ"); for actives, LEDs and connectors it is the MPN, paired with a
    # short descriptor as the secondary line (config.MPN_HEADLINE_TYPES).
    if ctype in config.MPN_HEADLINE_TYPES:
        headline = mpn_id
        if ctype == "connector" and (pin_count != "—" or pitch != "—"):
            subline = f"{pin_count} pins · {pitch}"
        else:
            subline = (
                _spec_value(raw_specs, spec_cfg.get("descriptor", []))
                or description
            )
    else:
        headline = value
        subline = mpn_id

    # Resistors get a colour-band diagram (inline SVG) in place of a pinout
    # image; "" for every other type so the simple template falls back.
    band_svg = ""
    if ctype == "resistor":
        band_svg = resistor_band_svg(
            _spec_value(raw_specs, spec_cfg.get("value", [])),
            _spec_value(raw_specs, ["tolerance"]),
        )

    context: dict[str, Any] = {
        "type_colour": colour,
        "bar_text_colour": bar_text_colour(colour),
        "type_name": config.type_display_name(ctype),
        "mpn": mpn_id,
        # Generic hierarchy fields the templates render (largest / secondary).
        "headline": headline,
        "subline": subline,
        "description": description,
        "value": value,
        "specs": specs,
        "pin_count": pin_count,
        "pitch": pitch,
        # Inline resistor colour-band SVG (empty for non-resistors).
        "band_svg": band_svg,
        # A stored package photo wins; otherwise fall back to a bundled package
        # outline diagram (pin names relabelled per component type).
        "package_image": _template_uri(package_image_path(record)),
        "pinout_image": _template_uri(record.get("pinout_image_path")),
        "qr_image": _template_uri(_generate_qr(mpn, record.get("datasheet_url"))),
    }
    context.update(merged)
    return context, template_name


def build_label(mpn: str, overrides: dict[str, Any] | None = None) -> str:
    """Render the print-ready label HTML for a cached component.

    Thin wrapper over label_context(): see it for override semantics. Image
    overrides must be URIs resolvable from the templates/ directory.
    """
    context, template_name = label_context(mpn, overrides)
    return _env.get_template(template_name).render(context)


# ── Generic groups (no MPN, no cache, rendered on the fly) ──────────────────────

def build_generic_label(
    group: dict[str, Any],
    value: dict[str, Any],
    params: dict[str, str] | None = None,
) -> str:
    """Render label HTML for one value of a generic component group.

    `group` is a generic_groups record (see cache.get_generic_group), `value`
    is one element of group["values"], and `params` maps each parameter key to
    the option the user picked at print time (e.g. {"power": "1/4 W"}). Nothing
    is read from or written to the cache — generic values carry no MPN and store
    no per-value record; this is the on-the-fly counterpart to build_label().

    It reuses the same templates, colour logic and resistor colour-band SVG as
    real parts. Each parameter's `role` (config'd in core/generics.py) decides
    how the picked option is applied to the label:
      "headline" → appended to the headline (a cap's voltage → "100 µF 50 V")
      "spec"     → shown as a spec row (a resistor's power rating)
    Per-value specs (diode ratings) and the group's fixed_specs follow; the
    simple template shows the first three. No MPN, QR, package or pinout image
    is shown — the value itself is the identifier.
    """
    params = params or {}
    ctype = group.get("component_type")
    colour = group.get("colour") or config.type_colour(ctype)

    headline = value["value"]
    spec_rows: list[dict[str, str]] = []
    for param in group.get("parameters") or []:
        chosen = params.get(param.get("key", ""))
        if not chosen:
            continue
        if param.get("role") == "headline":
            headline = f"{headline} {chosen}"
        else:
            spec_rows.append({"name": param["name"], "value": chosen})
    spec_rows += value.get("specs") or []        # per-value specs (e.g. diode V/I)
    spec_rows += group.get("fixed_specs") or []   # specs shown on every label

    # Resistors get the colour-band diagram in the image zone, exactly as real
    # resistor labels do; the tolerance comes from the group's fixed_specs.
    band_svg = ""
    if ctype == "resistor":
        tol = next(
            (s.get("value") for s in (group.get("fixed_specs") or [])
             if _norm(s.get("name") or "").startswith("tol")),
            None,
        )
        band_svg = resistor_band_svg(value["value"], tol)

    # Full context superset (StrictUndefined-safe for any label_template). The
    # secondary line shows the family name in place of the (absent) MPN.
    context: dict[str, Any] = {
        "type_colour": colour,
        "bar_text_colour": bar_text_colour(colour),
        "type_name": config.type_display_name(ctype),
        "mpn": "",
        "headline": headline,
        "subline": group.get("display_name") or "",
        "description": group.get("display_name") or "",
        "value": headline,
        "specs": spec_rows,
        "pin_count": "—",
        "pitch": "—",
        "band_svg": band_svg,
        "package_image": None,
        "pinout_image": None,
        "qr_image": None,
    }
    template_name = group.get("label_template") or config.type_template(ctype)
    return _env.get_template(template_name).render(context)

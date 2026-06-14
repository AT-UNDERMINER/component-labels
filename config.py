"""
config.py — central configuration for the Component Label System.

Holds, in one place:
  • Filesystem paths        (derived from PROJECT_ROOT, env-overridable)
  • API endpoints/credentials (env only — no secrets are ever hardcoded here)
  • Avery 45x45-S sheet geometry constants (millimetres)
  • Component type → colour-bar map

Design rules enforced here (from CLAUDE.md):
  - No absolute paths are baked in; everything hangs off PROJECT_ROOT so the
    whole folder can be relocated without breaking references.
  - Every tunable value can be overridden by an environment variable / .env
    entry, so the app behaves identically on bare metal, a VM, or in Docker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Load a .env file if python-dotenv is installed. It is intentionally optional
# so that this module imports cleanly before dependencies are installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv not yet installed
    pass


# ── Small env helpers ─────────────────────────────────────────────────────────

def _path_from_env(var: str, default_rel: str) -> Path:
    """Return a Path from env var `var`, else PROJECT_ROOT/`default_rel`.

    A relative value in the env var is resolved against PROJECT_ROOT; an
    absolute value is honoured as-is (useful for Docker volume mounts).
    """
    val = os.getenv(var)
    if val:
        p = Path(val)
        return p if p.is_absolute() else (PROJECT_ROOT / p)
    return PROJECT_ROOT / default_rel


def _float_from_env(var: str, default: float) -> float:
    """Read a float from env var `var`, falling back to `default` if unset/invalid."""
    val = os.getenv(var)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _int_from_env(var: str, default: int) -> int:
    val = os.getenv(var)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# ── Paths ─────────────────────────────────────────────────────────────────────
# PROJECT_ROOT is the directory this file lives in. Every other path derives
# from it, so paths stored in the DB can be kept relative to this root.

PROJECT_ROOT = Path(__file__).resolve().parent

DB_PATH = _path_from_env("DB_PATH", "db/components.db")


# ── Persisted UI settings overrides (db/settings.json) ─────────────────────────
# The web UI's /settings page writes sheet-geometry and colour-bar overrides
# here so a tweak made in the browser survives a restart. They are layered over
# the env/defaults below, with JSON winning, by _geo_*() and the colour loop.
# The file is optional: absent → pure env/default behaviour (unchanged). API
# credentials are NEVER stored here — they stay environment-only.

SETTINGS_PATH = DB_PATH.parent / "settings.json"


def _load_settings() -> dict:
    """Read db/settings.json, or {} if absent/corrupt (must never break startup)."""
    try:
        if SETTINGS_PATH.is_file():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


_SETTINGS = _load_settings()


def _geo_float(name: str, default: float) -> float:
    """Geometry value with precedence: settings.json > env var > built-in default."""
    geo = _SETTINGS.get("geometry") or {}
    if name in geo:
        try:
            return float(geo[name])
        except (TypeError, ValueError):
            pass
    return _float_from_env(name, default)


def _geo_int(name: str, default: int) -> int:
    """Integer geometry value with the same precedence as _geo_float()."""
    geo = _SETTINGS.get("geometry") or {}
    if name in geo:
        try:
            return int(geo[name])
        except (TypeError, ValueError):
            pass
    return _int_from_env(name, default)


OUTPUT_DIR = _path_from_env("OUTPUT_DIR", "output")
DATASHEET_DIR = OUTPUT_DIR / "datasheets"   # cached downloaded PDFs
IMAGE_DIR = OUTPUT_DIR / "images"           # extracted pinout / package images
LABEL_DIR = OUTPUT_DIR / "labels"           # generated PDF label sheets

TEMPLATE_DIR = PROJECT_ROOT / "templates"   # Jinja2 label templates + shared CSS
PACKAGE_DIR = TEMPLATE_DIR / "packages"     # bundled package outline SVGs


# ── Nexar / Octopart API ──────────────────────────────────────────────────────
# Secrets come from the environment ONLY. There are deliberately no default
# credential values here (the old prototype hardcoded them — that must not
# carry over). Endpoint URLs are not secret, so they get safe defaults.

NEXAR_CLIENT_ID = os.getenv("NEXAR_CLIENT_ID")
NEXAR_CLIENT_SECRET = os.getenv("NEXAR_CLIENT_SECRET")
NEXAR_TOKEN_URL = os.getenv("NEXAR_TOKEN_URL", "https://identity.nexar.com/connect/token")
NEXAR_API_URL = os.getenv("NEXAR_API_URL", "https://api.nexar.com/graphql")


# ── Web UI / QR codes ─────────────────────────────────────────────────────────
# QR codes on labels link to http://<QR_HOST>/component/<mpn>. Set QR_HOST to
# an address phones can actually reach — the server's LAN IP or Tailscale
# name plus the web UI port (e.g. "labelpi.tail1234.ts.net:5000").

QR_HOST = os.getenv("QR_HOST", "localhost:5000")


# ── Avery 45×45-S sheet geometry (all values in millimetres) ──────────────────
# These are configurable constants, not magic numbers: the renderer reads them
# from here so the user can fine-tune to their printer's offset via .env.

SHEET_WIDTH_MM = _geo_float("SHEET_WIDTH_MM", 210.0)    # A4 width
SHEET_HEIGHT_MM = _geo_float("SHEET_HEIGHT_MM", 297.0)  # A4 height

LABEL_WIDTH_MM = _geo_float("LABEL_WIDTH_MM", 45.0)
LABEL_HEIGHT_MM = _geo_float("LABEL_HEIGHT_MM", 45.0)

GRID_COLS = _geo_int("GRID_COLS", 4)
GRID_ROWS = _geo_int("GRID_ROWS", 5)

MARGIN_TOP_MM = _geo_float("MARGIN_TOP_MM", 13.0)   # sheet edge → first row
MARGIN_LEFT_MM = _geo_float("MARGIN_LEFT_MM", 10.0)  # sheet edge → first col
GAP_H_MM = _geo_float("GAP_H_MM", 5.0)              # horizontal gap between labels
GAP_V_MM = _geo_float("GAP_V_MM", 5.0)              # vertical gap between labels

# Print-finishing margins (millimetres), applied by core/pdf_renderer.py:
#   BLEED      — how far the label's background + top colour bar extend BEYOND
#                the 45mm trim so a slightly mis-registered printer never leaves
#                a white sliver at the label edge (colour runs to the very edge).
#   SAFE_ZONE  — how far all text/image content is kept INSIDE the trim, so the
#                same print offset can't clip a digit or a pin label. Passed into
#                the label CSS as the `--safe-zone` custom property.
BLEED_MM = _geo_float("BLEED_MM", 1.0)
SAFE_ZONE_MM = _geo_float("SAFE_ZONE_MM", 2.0)

# Total label slots on one sheet. Positions are numbered 1..LABELS_PER_SHEET,
# left-to-right then top-to-bottom (1 = top-left, 20 = bottom-right).
LABELS_PER_SHEET = GRID_COLS * GRID_ROWS


# ── Component types & colour coding ───────────────────────────────────────────
# Single source of truth for component types. To add a new type, add one row
# here — display name + hex colour for the label's top colour bar. No other
# code needs to change.

# One row per component type: (key, display name, label-bar colour, template).
_SIMPLE = "label_simple.html"
_COMPLEX = "label_complex.html"
_CONNECTOR = "label_connector.html"

_TYPE_TABLE: list[tuple[str, str, str, str]] = [
    ("resistor",               "Resistor",         "#8B4513", _SIMPLE),     # brown
    ("capacitor_electrolytic", "Electrolytic Cap", "#4169E1", _SIMPLE),     # royal blue
    ("capacitor_ceramic",      "Ceramic Cap",      "#1E90FF", _SIMPLE),     # dodger blue
    ("zener_diode",            "Zener Diode",      "#FF8C00", _SIMPLE),     # dark orange
    ("bjt_transistor",         "BJT Transistor",   "#228B22", _COMPLEX),    # forest green
    ("mosfet",                 "MOSFET",           "#006400", _COMPLEX),    # dark green
    ("ic_opamp",               "IC / Op-Amp",      "#8B008B", _COMPLEX),    # dark magenta
    ("led",                    "LED",              "#FFD700", _SIMPLE),     # gold
    ("inductor",               "Inductor",         "#708090", _SIMPLE),     # slate grey
    ("connector",              "Connector",        "#DC143C", _CONNECTOR),  # crimson
]

COMPONENT_TYPES: dict[str, dict[str, str]] = {
    key: {"display_name": name, "colour": colour, "template": template}
    for key, name, colour, template in _TYPE_TABLE
}

# Apply any colour-bar overrides saved by the web UI (settings.json > _TYPE_TABLE).
# Only the colour is overridable here; display name + template stay code-defined.
for _key, _hex in (_SETTINGS.get("colours") or {}).items():
    if _key in COMPONENT_TYPES and isinstance(_hex, str):
        COMPONENT_TYPES[_key]["colour"] = _hex

# Fallbacks used when a component's type is unknown or missing. The complex
# template is the safest default — it has slots for everything.
DEFAULT_COLOUR = "#333333"
DEFAULT_TEMPLATE = _COMPLEX


def type_colour(component_type: str | None) -> str:
    """Hex colour for a component type's label bar, or DEFAULT_COLOUR if unknown."""
    entry = COMPONENT_TYPES.get(component_type or "")
    return entry["colour"] if entry else DEFAULT_COLOUR


def type_display_name(component_type: str | None) -> str:
    """Human-readable name for a component type, falling back to the raw key."""
    entry = COMPONENT_TYPES.get(component_type or "")
    if entry:
        return entry["display_name"]
    return component_type or "Unknown"


def type_template(component_type: str | None) -> str:
    """Label template filename for a component type (DEFAULT_TEMPLATE if unknown)."""
    entry = COMPONENT_TYPES.get(component_type or "")
    return entry["template"] if entry else DEFAULT_TEMPLATE


# ── Information hierarchy: what the headline (largest text) should be ──────────
# The headline is the fastest way to identify a part at a glance, and that
# differs by type. Passives (resistor, caps, zener, inductor) are identified by
# their value, so the value leads and the MPN is secondary. Actives, LEDs and
# connectors are identified by their part number, so the MPN leads with a short
# descriptor (polarity / channel / colour / pins·pitch) as the secondary line.
#
# Types listed here lead with the MPN; every other type leads with its value.
# label_builder.py reads this to fill the generic `headline`/`subline` fields.
MPN_HEADLINE_TYPES: set[str] = {
    "bjt_transistor",
    "mosfet",
    "ic_opamp",
    "led",
    "connector",
}


# ── Key specs per component type (brief: "Key Specs Per Component Type") ──────
# Which Nexar spec attributes each label surfaces. Match terms are checked,
# normalised to lowercase alphanumerics, as substrings of the API attribute's
# shortname/name — list the most specific candidates first.
#
#   value       matchers for the headline value line on value-led types
#               (e.g. "10 kΩ"); see MPN_HEADLINE_TYPES for the distinction
#   value2      optional second value joined onto the headline with a space
#               (e.g. a capacitor's voltage rating → "100 µF 50 V")
#   descriptor  matchers for the secondary line on MPN-led types
#               (e.g. a BJT's polarity, a MOSFET's channel, an LED's colour)
#   specs       (display label, matchers) — the first 3 found are shown, in
#               this order, so later entries act as stand-ins for missing ones
#   pins / pitch   matchers for the connector template's stat boxes
#
# A type with no entry here simply gets no specs row — nothing breaks.
# Note: a value promoted to the descriptor is deliberately left out of `specs`
# so it is not shown twice on the same label.

LABEL_SPECS: dict[str, dict] = {
    "resistor": {
        "value": ["resistance"],
        "specs": [
            ("Power", ["powerrating", "power"]),
            ("Tolerance", ["tolerance"]),
        ],
    },
    "capacitor_electrolytic": {
        # Headline combines capacitance + voltage (e.g. "100 µF 50 V"), so the
        # voltage is promoted to value2 and left out of the specs row below.
        "value": ["capacitance"],
        "value2": ["voltagerating", "ratedvoltage", "voltage"],
        "specs": [
            ("Temp", ["operatingtemperature", "temperature"]),
            ("Tolerance", ["tolerance"]),
        ],
    },
    "capacitor_ceramic": {
        "value": ["capacitance"],
        "value2": ["voltagerating", "ratedvoltage", "voltage"],
        "specs": [
            ("Dielectric", ["dielectric", "temperaturecoefficient"]),
            ("Tolerance", ["tolerance"]),
        ],
    },
    "zener_diode": {
        "value": ["zenervoltage", "voltagezener"],
        "specs": [
            ("Power", ["powerdissipation", "powerrating", "power"]),
            ("Tolerance", ["tolerance"]),
        ],
    },
    "led": {
        # MPN-led: the colour/wavelength is the secondary descriptor, not the
        # headline, so it lives under "descriptor" rather than "value".
        "descriptor": ["colour", "color", "wavelength"],
        "specs": [
            ("Forward V", ["forwardvoltage"]),
            ("Forward I", ["forwardcurrent"]),
        ],
    },
    "inductor": {
        "value": ["inductance"],
        "specs": [
            ("Current", ["currentrating", "ratedcurrent", "current"]),
            ("DCR", ["dcresistance", "dcr"]),
        ],
    },
    "bjt_transistor": {
        # Polarity (NPN/PNP) is the headline descriptor, so it is omitted here.
        "descriptor": ["polarity", "transistortype"],
        "specs": [
            ("Vceo", ["collectoremittervoltage", "vceo"]),
            ("Ic max", ["collectorcurrent", "icmax"]),
            ("hFE", ["dccurrentgain", "hfe"]),
        ],
    },
    "mosfet": {
        # Channel (N/P) is the headline descriptor, so it is omitted here.
        "descriptor": ["channeltype", "fettype", "transistortype"],
        "specs": [
            ("Vds", ["drainsourcevoltage", "vds"]),
            ("Id max", ["continuousdraincurrent", "draincurrent"]),
            ("Rds(on)", ["rdson", "drainsourceresistance"]),
        ],
    },
    "ic_opamp": {
        "specs": [
            ("Supply", ["supplyvoltage", "operatingsupplyvoltage"]),
            ("Bandwidth", ["gainbandwidth", "bandwidth"]),
            ("Slew Rate", ["slewrate"]),
        ],
    },
    "connector": {
        "pins": ["numberofpositions", "numberofpins", "numberofcontacts", "positions"],
        "pitch": ["pitch"],
        "specs": [
            ("Current", ["currentrating", "ratedcurrent"]),
            ("Voltage", ["voltagerating", "ratedvoltage"]),
            ("Temp", ["operatingtemperature"]),
        ],
    },
}


# ── Package → diagram SVG map ─────────────────────────────────────────────────
# Maps a component's package/case name (as it appears in the Nexar specs, e.g.
# "TO-92-3", "SOIC-8", "0805 (2012 Metric)") to a bundled outline SVG in
# templates/packages/. core.label_builder.resolve_package_image() normalises the
# spec value to lowercase alphanumerics and matches an alias as a substring,
# trying the longest alias first so "sot235" picks SOT-23-5 over SOT-23. List
# every spelling a provider might use; add a row + an SVG to support a new
# package — no other code changes needed.

PACKAGE_MAP: dict[str, str] = {
    # ── Through-hole ──
    "TO-92": "TO-92.svg",
    "TO92": "TO-92.svg",
    "TO-220": "TO-220.svg",
    "TO220": "TO-220.svg",
    "TO-3": "TO-3.svg",
    "TO3": "TO-3.svg",
    "DO-41": "DO-41.svg",
    "DO41": "DO-41.svg",
    "DO-204AL": "DO-41.svg",            # JEDEC name for DO-41
    "DIP-8": "DIP-8.svg",
    "PDIP-8": "DIP-8.svg",
    "8-DIP": "DIP-8.svg",
    "DIP-14": "DIP-14.svg",
    "PDIP-14": "DIP-14.svg",
    "14-DIP": "DIP-14.svg",
    "DIP-16": "DIP-16.svg",
    "PDIP-16": "DIP-16.svg",
    "16-DIP": "DIP-16.svg",
    # ── Surface-mount ──
    "SOT-23-5": "SOT-23-5.svg",         # must precede SOT-23 (longer alias wins)
    "SOT-25": "SOT-23-5.svg",           # 5-lead SOT-23 alias
    "SOT-23": "SOT-23.svg",
    "SOT23": "SOT-23.svg",
    "SOT-223": "SOT-223.svg",
    "SOT223": "SOT-223.svg",
    "SOIC-8": "SOIC-8.svg",
    "SOIC8": "SOIC-8.svg",
    "SO-8": "SOIC-8.svg",
    "SOP-8": "SOIC-8.svg",
    "0805": "0805.svg",
    "2012": "0805.svg",                 # metric code for 0805
    "0603": "0603.svg",
    "1608": "0603.svg",                 # metric code for 0603
}


# ── Package pin-name relabelling ──────────────────────────────────────────────
# A shared package outline (e.g. TO-92.svg) carries default class="pin-name"
# labels; label_builder relabels them per component type at render time via
# substitute_pin_names(). Keyed by (component_type, svg filename); the value is
# the pin names in pin order. A (type, package) pair not listed keeps the SVG's
# default labels — e.g. an op-amp DIP-8 needs no entry, as that outline only
# carries pin numbers (1–8) and no names.

PACKAGE_PIN_NAMES: dict[tuple[str, str], list[str]] = {
    ("bjt_transistor", "TO-92.svg"):  ["C", "B", "E"],
    ("mosfet",         "TO-92.svg"):  ["D", "G", "S"],
    ("mosfet",         "TO-220.svg"): ["G", "D", "S"],
    ("mosfet",         "SOT-23.svg"): ["G", "S", "D"],
}

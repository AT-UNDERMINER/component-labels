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

OUTPUT_DIR = _path_from_env("OUTPUT_DIR", "output")
DATASHEET_DIR = OUTPUT_DIR / "datasheets"   # cached downloaded PDFs
IMAGE_DIR = OUTPUT_DIR / "images"           # extracted pinout / package images
LABEL_DIR = OUTPUT_DIR / "labels"           # generated PDF label sheets

TEMPLATE_DIR = PROJECT_ROOT / "templates"   # Jinja2 label templates + shared CSS


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

SHEET_WIDTH_MM = _float_from_env("SHEET_WIDTH_MM", 210.0)    # A4 width
SHEET_HEIGHT_MM = _float_from_env("SHEET_HEIGHT_MM", 297.0)  # A4 height

LABEL_WIDTH_MM = _float_from_env("LABEL_WIDTH_MM", 45.0)
LABEL_HEIGHT_MM = _float_from_env("LABEL_HEIGHT_MM", 45.0)

GRID_COLS = _int_from_env("GRID_COLS", 4)
GRID_ROWS = _int_from_env("GRID_ROWS", 5)

MARGIN_TOP_MM = _float_from_env("MARGIN_TOP_MM", 13.0)   # sheet edge → first row
MARGIN_LEFT_MM = _float_from_env("MARGIN_LEFT_MM", 10.0)  # sheet edge → first col
GAP_H_MM = _float_from_env("GAP_H_MM", 5.0)              # horizontal gap between labels
GAP_V_MM = _float_from_env("GAP_V_MM", 5.0)              # vertical gap between labels

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


# ── Key specs per component type (brief: "Key Specs Per Component Type") ──────
# Which Nexar spec attributes each label surfaces. Match terms are checked,
# normalised to lowercase alphanumerics, as substrings of the API attribute's
# shortname/name — list the most specific candidates first.
#
#   value   matchers for the simple label's big value line (e.g. "10 kΩ")
#   specs   (display label, matchers) — the first 3 found are shown, in
#           this order, so later entries act as stand-ins for missing ones
#   pins / pitch   matchers for the connector template's stat boxes
#
# A type with no entry here simply gets no specs row — nothing breaks.

LABEL_SPECS: dict[str, dict] = {
    "resistor": {
        "value": ["resistance"],
        "specs": [
            ("Power", ["powerrating", "power"]),
            ("Tolerance", ["tolerance"]),
        ],
    },
    "capacitor_electrolytic": {
        "value": ["capacitance"],
        "specs": [
            ("Voltage", ["voltagerating", "ratedvoltage", "voltage"]),
            ("Temp", ["operatingtemperature", "temperature"]),
        ],
    },
    "capacitor_ceramic": {
        "value": ["capacitance"],
        "specs": [
            ("Voltage", ["voltagerating", "ratedvoltage", "voltage"]),
            ("Dielectric", ["dielectric", "temperaturecoefficient"]),
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
        "value": ["colour", "color", "wavelength"],
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
        "specs": [
            ("Polarity", ["polarity", "transistortype"]),
            ("Vceo", ["collectoremittervoltage", "vceo"]),
            ("Ic max", ["collectorcurrent", "icmax"]),
            ("hFE", ["dccurrentgain", "hfe"]),
        ],
    },
    "mosfet": {
        "specs": [
            ("Channel", ["channeltype", "fettype", "transistortype"]),
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

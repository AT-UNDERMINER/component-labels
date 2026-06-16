"""
core/generics.py — the generic component catalogue.

A "generic group" is a pre-defined component family with standard parameters
and no specific MPN or API lookup — e.g. "Metal Film 1% resistors (E96, 1Ω–10MΩ)"
or "Rectifier 1N400x". At print time the user picks parameter value(s) (power,
voltage, …) and ticks the catalogue values they want; each ticked value becomes
one label, generated on the fly. Nothing per value is ever stored in the DB.

This module owns only the *catalogue data*:
  • the E-series base value tables and the value-formatting helpers,
  • `seed_definitions()` — the full list of group definitions used to seed the
    `generic_groups` table on first run (see core/cache.seed_generic_groups()).

It is intentionally free of any database or rendering dependency (it imports
nothing from the rest of the project), so:
  • core/cache.py can import it at seed time without a circular import, and
  • turning a (group, value, parameters) triple into label HTML lives in
    core/label_builder.build_generic_label(), keeping data and rendering apart.

Data shapes
-----------
A group definition (one element of seed_definitions()):

    {
      "key":            "resistor_metal_film_1pct",   # unique slug
      "display_name":   "Metal Film 1%",
      "component_type": "resistor",                    # → colour / chip / template
      "label_template": "label_simple.html",
      "colour":         None,                          # None → inherit type colour
      "parameters": [                                  # filter stages, user-picked
        {"key": "power", "name": "Power", "role": "spec",
         "options": ["1/8 W", "1/4 W", "1/2 W", "1 W"]},
      ],
      "values": [                                      # the value axis (checkboxes)
        {"value": "1 Ω", "ohms": 1.0}, ...
      ],
      "fixed_specs": [                                 # specs shown on every label
        {"name": "Tolerance", "value": "1%"},
      ],
    }

A parameter's `role` decides how the user's pick is applied to each label
(label_builder reads this, so the catalogue stays declarative):
    "spec"      → shown as a spec row (e.g. resistor Power)
    "headline"  → appended to the headline (e.g. a cap's selected voltage, so the
                  headline reads "100 µF 50 V")
"""

from __future__ import annotations

import math


# ── E-series base values (one decade, normalised to 1.00–9.76) ─────────────────

E6 = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]

E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]

E24 = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
]

E96 = [
    1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
    1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
    1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
    2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
    3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
    4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
    5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
    7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
]


def _eseries(bases: list[float], lo: float, hi: float) -> list[float]:
    """Every ``base × 10^k`` within [lo, hi] inclusive, ascending.

    `bases` are the normalised decade values (1.00–9.76); the decade exponent
    k is swept wide enough to cover the range, and a small relative tolerance
    absorbs float error at the endpoints so e.g. exactly 10 MΩ is included.
    """
    out: list[float] = []
    kmin = math.floor(math.log10(lo)) - 1
    kmax = math.ceil(math.log10(hi)) + 1
    for k in range(kmin, kmax + 1):
        scale = 10.0 ** k
        for b in bases:
            v = b * scale
            if lo * (1 - 1e-9) <= v <= hi * (1 + 1e-9):
                out.append(v)
    out.sort()
    return out


# ── Value formatting ───────────────────────────────────────────────────────────

def _trim(v: float) -> str:
    """Compact decimal: up to 3 dp, trailing zeros removed (1.0→"1", 1.02→"1.02")."""
    return f"{v:.3f}".rstrip("0").rstrip(".")


def format_resistance(ohms: float) -> str:
    """Ohms → engineering string with the Ω symbol (e.g. 1020 → "1.02 kΩ")."""
    if ohms >= 1e6:
        return f"{_trim(ohms / 1e6)} MΩ"
    if ohms >= 1e3:
        return f"{_trim(ohms / 1e3)} kΩ"
    return f"{_trim(ohms)} Ω"


def format_capacitance(farads: float) -> str:
    """Farads → engineering string in pF/nF/µF (e.g. 1e-4 → "100 µF")."""
    pf = farads * 1e12
    if pf >= 1e6:
        return f"{_trim(pf / 1e6)} µF"
    if pf >= 1e3:
        return f"{_trim(pf / 1e3)} nF"
    return f"{_trim(pf)} pF"


def _resistor_values(bases: list[float], lo: float, hi: float) -> list[dict]:
    """Value-axis list for a resistor group: {"value", "ohms"} per E-series step."""
    return [{"value": format_resistance(r), "ohms": round(r, 6)}
            for r in _eseries(bases, lo, hi)]


def _capacitor_values(bases: list[float], lo: float, hi: float) -> list[dict]:
    """Value-axis list for a capacitor group: {"value", "farads"} per E-series step."""
    return [{"value": format_capacitance(f), "farads": f}
            for f in _eseries(bases, lo, hi)]


# ── Fixed diode catalogues (value IS the part number; specs baked per part) ─────

def _diode(part: str, *specs: tuple[str, str]) -> dict:
    """One fixed-value diode: the part number is the value; specs are per-part."""
    return {"value": part,
            "specs": [{"name": n, "value": v} for n, v in specs]}


# Rectifier 1N400x — all 1 A, reverse voltage (V_RRM) climbs with the suffix.
# Per-part specs use the underscore notation (V_RRM, I_F) that label_builder
# renders as proper subscripts; the shared forward drop / surge live in
# fixed_specs (V_F, I_FSM) below.
_RECTIFIER_1N400X = [
    _diode("1N4001", ("V_RRM", "50 V"), ("I_F", "1 A")),
    _diode("1N4002", ("V_RRM", "100 V"), ("I_F", "1 A")),
    _diode("1N4003", ("V_RRM", "200 V"), ("I_F", "1 A")),
    _diode("1N4004", ("V_RRM", "400 V"), ("I_F", "1 A")),
    _diode("1N4005", ("V_RRM", "600 V"), ("I_F", "1 A")),
    _diode("1N4006", ("V_RRM", "800 V"), ("I_F", "1 A")),
    _diode("1N4007", ("V_RRM", "1000 V"), ("I_F", "1 A")),
]

# Schottky 1N581x — all 1 A, low forward drop (V_F, the defining feature).
_SCHOTTKY_1N581X = [
    _diode("1N5817", ("V_R", "20 V"), ("I_F", "1 A")),
    _diode("1N5818", ("V_R", "30 V"), ("I_F", "1 A")),
    _diode("1N5819", ("V_R", "40 V"), ("I_F", "1 A")),
]

# Zener 1N47xxA — full 1 W series; zener voltage per part (Power is a fixed spec).
_ZENER_1N47XXA_VOLTAGES = [
    "3.3 V", "3.6 V", "3.9 V", "4.3 V", "4.7 V", "5.1 V", "5.6 V", "6.2 V",
    "6.8 V", "7.5 V", "8.2 V", "9.1 V", "10 V", "11 V", "12 V", "13 V",
    "15 V", "16 V", "18 V", "20 V", "22 V", "24 V", "27 V", "30 V", "33 V",
    "36 V", "39 V", "43 V", "47 V", "51 V", "56 V", "62 V", "68 V", "75 V",
    "82 V", "91 V", "100 V",
]


def _zener_1n47xxa() -> list[dict]:
    """1N4728A..1N4764A: 37 parts, value = part number, V_Z spec per part."""
    return [
        _diode(f"1N{4728 + i}A", ("V_Z", vz))
        for i, vz in enumerate(_ZENER_1N47XXA_VOLTAGES)
    ]


# ── Seed definitions ────────────────────────────────────────────────────────────

def seed_definitions() -> list[dict]:
    """The full list of generic-group definitions to seed on first run.

    Pure data — computed fresh each call from the E-series tables and the fixed
    diode catalogues above. core/cache.seed_generic_groups() inserts each one
    (keyed on the unique `key`, idempotently), serialising the parameters /
    values / fixed_specs lists to their *_json columns.
    """
    return [
        # ── Resistors ──────────────────────────────────────────────────────────
        {
            "key": "resistor_metal_film_1pct",
            "display_name": "Metal Film 1%",
            "component_type": "resistor",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "power", "name": "Power", "role": "spec",
                 "options": ["1/8 W", "1/4 W", "1/2 W", "1 W"]},
            ],
            "values": _resistor_values(E96, 1.0, 10e6),
            "fixed_specs": [{"name": "Tolerance", "value": "1%"}],
        },
        {
            "key": "resistor_carbon_film_5pct",
            "display_name": "Carbon Film 5%",
            "component_type": "resistor",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "power", "name": "Power", "role": "spec",
                 "options": ["1/4 W", "1/2 W", "1 W"]},
            ],
            "values": _resistor_values(E24, 1.0, 10e6),
            "fixed_specs": [{"name": "Tolerance", "value": "5%"}],
        },
        {
            "key": "resistor_wirewound",
            "display_name": "Wirewound",
            "component_type": "resistor",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "power", "name": "Power", "role": "spec",
                 "options": ["1 W", "2 W", "5 W", "10 W"]},
            ],
            "values": _resistor_values(E24, 1.0, 100e3),
            "fixed_specs": [{"name": "Tolerance", "value": "5%"}],
        },
        # ── Electrolytic capacitors ────────────────────────────────────────────
        {
            "key": "cap_electrolytic_gp_85c",
            "display_name": "Electrolytic GP 85°C",
            "component_type": "capacitor_electrolytic",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "voltage", "name": "Voltage", "role": "headline",
                 "options": ["6.3 V", "10 V", "16 V", "25 V", "35 V",
                             "50 V", "63 V", "100 V"]},
            ],
            "values": _capacitor_values(E6, 1e-6, 10000e-6),
            "fixed_specs": [
                {"name": "Temp", "value": "85 °C"},
                {"name": "Tol", "value": "±20%"},
            ],
        },
        {
            "key": "cap_electrolytic_ht_105c",
            "display_name": "Electrolytic HT 105°C",
            "component_type": "capacitor_electrolytic",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "voltage", "name": "Voltage", "role": "headline",
                 "options": ["6.3 V", "10 V", "16 V", "25 V", "35 V",
                             "50 V", "63 V", "100 V"]},
            ],
            "values": _capacitor_values(E6, 1e-6, 10000e-6),
            "fixed_specs": [
                {"name": "Temp", "value": "105 °C"},
                {"name": "Tol", "value": "±20%"},
            ],
        },
        # ── Ceramic capacitors ─────────────────────────────────────────────────
        {
            "key": "cap_ceramic_x7r",
            "display_name": "Ceramic X7R",
            "component_type": "capacitor_ceramic",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "voltage", "name": "Voltage", "role": "headline",
                 "options": ["6.3 V", "10 V", "16 V", "25 V", "50 V", "100 V"]},
            ],
            "values": _capacitor_values(E12, 1e-12, 100e-6),
            "fixed_specs": [
                {"name": "Dielectric", "value": "X7R"},
                {"name": "Tol", "value": "±10%"},
            ],
        },
        {
            "key": "cap_ceramic_x5r",
            "display_name": "Ceramic X5R",
            "component_type": "capacitor_ceramic",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [
                {"key": "voltage", "name": "Voltage", "role": "headline",
                 "options": ["6.3 V", "10 V", "16 V", "25 V", "50 V", "100 V"]},
            ],
            "values": _capacitor_values(E12, 1e-12, 100e-6),
            "fixed_specs": [
                {"name": "Dielectric", "value": "X5R"},
                {"name": "Tol", "value": "±10%"},
            ],
        },
        # ── Diode series (fixed values, no parameters) ─────────────────────────
        {
            "key": "diode_rectifier_1n400x",
            "display_name": "Rectifier 1N400x",
            "component_type": "rectifier_diode",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [],
            "values": _RECTIFIER_1N400X,
            # Forward drop / surge are common to the whole series; the underscore
            # notation renders as V_F → V<sub>F</sub>, I_FSM → I<sub>FSM</sub>.
            "fixed_specs": [
                {"name": "V_F", "value": "1.1 V"},
                {"name": "I_FSM", "value": "30 A"},
            ],
        },
        {
            "key": "diode_zener_1n47xxa",
            "display_name": "Zener 1N47xxA",
            "component_type": "zener_diode",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [],
            "values": _zener_1n47xxa(),
            "fixed_specs": [{"name": "Power", "value": "1 W"}],
        },
        {
            "key": "diode_schottky_1n581x",
            "display_name": "Schottky 1N581x",
            "component_type": "schottky_diode",
            "label_template": "label_simple.html",
            "colour": None,
            "parameters": [],
            "values": _SCHOTTKY_1N581X,
            # Low forward drop is the defining Schottky feature; V_F → V<sub>F</sub>.
            "fixed_specs": [{"name": "V_F", "value": "0.45 V"}],
        },
    ]

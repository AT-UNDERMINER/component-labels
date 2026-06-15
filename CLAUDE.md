# Component Label System — Project Brief for Claude Code

## Project Overview

A fully automated electronic component label generation system. The user supplies one
or more part numbers via CLI or web UI; the system looks up component data, matches the
part's package to a bundled outline diagram, generates print-ready PDF labels
formatted for Avery 45×45-S sheets, and stores everything locally so no repeat API
calls are needed.

The goal is maximum automation: give it a part number, get a print-ready label. When
the package can't be matched automatically the part is flagged **needs_review** rather
than guessed at, so the only manual step is approving those few in the web editor
(optionally swapping in a package/pinout image first).

---

## Target Platform

- **OS:** Linux (Ubuntu/Debian — running on a mini PC home server)
- **Interface:** CLI (automation/batch) + web UI served over local network / Tailscale
- **Output:** PDF, print-ready, pixel-perfect for Avery 45×45-S label sheets
- **Printing:** PDF is downloaded from the web UI and printed from a Windows machine
  connected to the label printer — no server-side print driver needed
- **Deployment:** Designed to be portable across bare-metal Linux, Proxmox VM, and
  Docker container with no code changes required

---

## Avery 45×45-S Sheet Specification

- **Sheet size:** A4 (210mm × 297mm)
- **Label size:** 45mm × 45mm
- **Grid:** 4 columns × 5 rows = 20 labels per sheet
- **Layout constants** (to be measured/confirmed against physical sheet):
  - Top margin: ~13mm
  - Left margin: ~10mm
  - Horizontal gap between labels: ~5mm
  - Vertical gap between labels: ~5mm
- The PDF renderer must treat these as configurable constants, not hardcoded magic
  numbers, so the user can fine-tune to their specific printer's offset.
- **Start offset feature:** User can specify which label position (1–20) to begin
  printing from, so partially used sheets are not wasted. Label positions are
  numbered left-to-right, top-to-bottom (1 = top-left, 20 = bottom-right).

---

## Architecture

```
component-labels/
├── CLAUDE.md                  ← this file
├── main.py                    ← CLI entry point
├── app.py                     ← Flask/FastAPI web UI entry point
├── config.py                  ← sheet geometry, colour map, API credentials
├── requirements.txt
│
├── core/
│   ├── lookup.py              ← Nexar/Octopart API queries + auth
│   ├── datasheet.py           ← PDF download only (nothing is extracted from it)
│   ├── cache.py               ← SQLite read/write layer
│   ├── label_builder.py       ← Populates label template with component data
│   └── pdf_renderer.py        ← Renders label grid to PDF using ReportLab or WeasyPrint
│
├── templates/
│   ├── label_simple.html      ← Template for simple components (resistor, cap, LED)
│   ├── label_complex.html     ← Template for complex ICs (op-amp, MOSFET, BJT)
│   ├── label_connector.html   ← Template for connectors (JST etc.)
│   ├── label_styles.css       ← shared layout CSS (mm units) used by all templates
│   └── preview.html           ← static browser preview of all templates at 3× scale
│
├── web/
│   ├── templates/             ← Jinja2 HTML for the web UI
│   └── static/                ← CSS, JS for web UI
│
├── db/
│   └── components.db          ← SQLite database (auto-created on first run)
│
└── output/
    ├── datasheets/            ← Cached downloaded PDFs (for the web UI / QR link)
    ├── images/                ← Per-part generated assets (QR PNG, relabelled
    │                            package SVG) + manual image uploads
    └── labels/                ← Generated PDF label sheets
```

---

## Data Pipeline (per part number)

1. **Cache check** — query SQLite for existing record; if found, skip to step 5
2. **API lookup** — authenticate with Nexar OAuth2, run GraphQL query for part data
3. **Datasheet download** — fetch PDF to `output/datasheets/`. The PDF is cached
   only so the web UI can link a local copy; **nothing is extracted from it**.
4. **Package resolution** (`core/label_builder.package_review_status()`):
   - Match the part's package/case spec against the bundled outline SVG library
     (`templates/packages/`, via `resolve_package_image()`)
   - **Match found** → `status = 'ready'` (the SVG is the label's diagram)
   - **No SVG for the package** → `status = 'needs_review'`,
     `review_reason = "No package diagram found for '<package>'"`
   - **No package spec at all** → `status = 'needs_review'`,
     `review_reason = "Package not identified"`
   - A part that already has a manually-set `package_image_path` stays `ready`
     (a `--refresh` never downgrades a part the user already approved)
5. **Cache write** — store all data + `status` / `review_reason` to SQLite
6. **Label build** — select template based on component type, populate with data
7. **PDF render** — place label(s) onto A4 sheet at correct grid position(s)

> There is no automatic pinout/package **extraction** from datasheets — the old
> keyword-scoring PDF rasteriser has been removed. Diagrams come from the curated
> package-outline SVG library, and anything it can't match is surfaced for review
> instead of being filled with a low-confidence guess.

---

## SQLite Schema

### `components` table
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| mpn | TEXT UNIQUE | manufacturer part number |
| manufacturer | TEXT | |
| component_type | TEXT | see component types below |
| description | TEXT | |
| specs_json | TEXT | full specs as JSON string |
| datasheet_url | TEXT | |
| datasheet_path | TEXT | local path to cached PDF |
| pinout_image_path | TEXT | manual override only (never auto-populated) |
| package_image_path | TEXT | manual package image/photo override if set |
| overrides_json | TEXT | saved web-editor state (template/headline/specs/…) |
| status | TEXT | `'ready'` (default) or `'needs_review'` |
| review_reason | TEXT | why it needs review (null once ready/approved) |
| created_at | TEXT | ISO timestamp |
| updated_at | TEXT | ISO timestamp |

`status` / `review_reason` are written together by `upsert_component()`: pass a
`status` to set both (the matching reason, or `None` to clear it on approval);
omit `status` (the default) to leave both untouched, so partial updates such as a
pinout override don't disturb the review flag. New DBs get the columns from the
schema; existing DBs are migrated by an `ALTER TABLE … ADD COLUMN` (same pattern
as `overrides_json`), which backfills old rows to `status = 'ready'`.

### `labels` table
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| mpn | TEXT | FK to components.mpn |
| label_html | TEXT | rendered HTML of the label |
| label_pdf_path | TEXT | path to generated single-label PDF |
| created_at | TEXT | |

### `generic_groups` table
Pre-defined component families (resistor/cap/diode series) with no MPN and no
API lookup — see **Generic Component System** below. Seeded on first run from
`core/generics.seed_definitions()` (idempotent `INSERT OR IGNORE` on `key`,
run from `cache.init_db()` via `seed_generic_groups()`).

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| key | TEXT UNIQUE | stable slug, e.g. `resistor_metal_film_1pct` |
| display_name | TEXT | e.g. "Metal Film 1%" |
| component_type | TEXT | drives colour / chip / template (a real type key) |
| parameters_json | TEXT | filter stages the user picks at print time (JSON) |
| values_json | TEXT | the full value list / value axis (JSON) |
| label_template | TEXT | which label template to render |
| fixed_specs_json | TEXT | specs shown on every label of the group (JSON) |
| colour | TEXT | label-bar colour; null → inherit `component_type`'s |
| created_at | TEXT | ISO timestamp |

Decoded by `cache._row_to_generic_group()` into friendlier `parameters` /
`values` / `fixed_specs` keys. Read helpers: `list_generic_groups(type=None)`,
`get_generic_group(key)`. Each `parameters` entry is
`{"key", "name", "options": [...], "role"}`, where `role` is `"spec"` (shown as
a spec row, e.g. resistor Power) or `"headline"` (appended to the headline, e.g.
a cap's selected Voltage → "100 µF 50 V").

---

## Component Types & Colour Coding

Each component type has a defined colour used for the top colour bar on the label.
Colours should be defined in `config.py` as a dict so new types can be added easily.

| Type key | Display name | Colour bar (suggested) |
|---|---|---|
| `resistor` | Resistor | `#8B4513` (brown) |
| `capacitor_electrolytic` | Electrolytic Cap | `#4169E1` (royal blue) |
| `capacitor_ceramic` | Ceramic Cap | `#1E90FF` (dodger blue) |
| `zener_diode` | Zener Diode | `#FF8C00` (dark orange) |
| `bjt_transistor` | BJT Transistor | `#228B22` (forest green) |
| `mosfet` | MOSFET | `#006400` (dark green) |
| `ic_opamp` | IC / Op-Amp | `#8B008B` (dark magenta) |
| `led` | LED | `#FFD700` (gold) |
| `inductor` | Inductor | `#708090` (slate grey) |
| `connector` | Connector | `#DC143C` (crimson) |

To add a new type: add one row to `_TYPE_TABLE` in `config.py` — (key, display
name, colour, template file) — plus optionally a `LABEL_SPECS` entry naming which
spec attributes to surface. No other code changes needed.

Bar text colour is not configured: `label_builder.bar_text_colour()` picks black
or white automatically by WCAG relative luminance, so light colours (e.g. gold)
get black text and dark colours get white.

---

## Label Layout — Two Templates

### Simple label (resistor, capacitor, LED, zener, inductor)
```
┌─────────────────────────────┐
│ [COLOUR BAR — type name]    │  ← ~8mm tall, coloured bg, white text
├─────────────────────────────┤
│ MPN (bold, large)           │
│ Value  (e.g. 10kΩ / 100nF) │
├──────────────┬──────────────┤
│ Spec 1       │ [Pinout img] │
│ Spec 2       │  (if avail)  │
│ Spec 3       │              │
└──────────────┴──────────────┘
```

### Complex label (BJT, MOSFET, IC, op-amp, connector)
```
┌─────────────────────────────┐
│ [COLOUR BAR — type name]    │
├──────────────┬──────────────┤
│ MPN (bold)   │ [Package img]│
│ Description  │  (physical)  │
├──────────────┴──────────────┤
│ Spec 1    Spec 2    Spec 3  │
├─────────────────────────────┤
│ [Pinout diagram image]      │
├─────────────────────────────┤
│ [QR code → datasheet URL]   │
└─────────────────────────────┘
```

Connector labels use a third template (`label_connector.html`) — like the complex
layout but with two prominent PINS / PITCH stat boxes between the header and specs.

Label templates are HTML/CSS rendered to PDF via WeasyPrint. Each template is a
standalone HTML file with Jinja2 placeholders; layout CSS is shared via
`templates/label_styles.css` (all physical mm units, sections budgeted to sum to
exactly 45mm). `templates/preview.html` shows all templates in a browser at 3×
scale with sample data — no PDF generation needed. Image URLs in rendered label
HTML are relative to `templates/`, so renderers must use that directory as their
base URL.

---

## Key Specs Per Component Type

These are the 2–3 specs to surface on simple labels. Pulled from Nexar `specs` array
by attribute shortname.

| Type | Key specs |
|---|---|
| Resistor | Resistance, Power Rating, Tolerance |
| Electrolytic Cap | Capacitance, Voltage Rating, Temperature |
| Ceramic Cap | Capacitance, Voltage Rating, Dielectric |
| Zener Diode | Zener Voltage, Power Dissipation, Tolerance |
| BJT Transistor | Polarity (NPN/PNP), Vceo, Ic max, hFE |
| MOSFET | Channel (N/P), Vds, Id max, Rds(on) |
| IC / Op-Amp | Supply Voltage, Bandwidth, Slew Rate |
| LED | Forward Voltage, Forward Current, Colour/Wavelength |
| Inductor | Inductance, Current Rating, DCR |
| Connector | # Pins, Pitch, Current Rating |

---

## PDF Rendering

- Use **WeasyPrint** (Python library) to render HTML/CSS → PDF
- Each label is rendered as an HTML fragment, then placed onto an A4 sheet grid
- Sheet geometry (margins, gaps, label size) all come from `config.py`
- Print job takes a list of `(mpn, grid_position)` tuples — positions 1–20
- **Start offset:** if user specifies `--start 5`, labels fill positions 5–20 then
  continue on next sheet; positions 1–4 are left blank
- Multi-sheet jobs are supported — one PDF file with multiple A4 pages
- **Bleed & safe zone** (config `BLEED_MM` / `SAFE_ZONE_MM`, editable on `/settings`):
  each cell gets a colour underlay that extends `BLEED_MM` past the trim so the
  top colour bar reaches the edge even if the printer is slightly off; the label
  artwork is scaled into the safe area via the `--safe-scale` / `--safe-zone` CSS
  custom properties (set on the cell, consumed by `.label` in `label_styles.css`)
  so all text/images stay `SAFE_ZONE_MM` inside the trim. Both vars are unset in
  the browser preview/editor, so a single label still renders at exactly 45×45mm.

---

## CLI Interface (`main.py`)

```bash
# Single part lookup + label generation
python main.py add NE555

# Batch from a text file (one MPN per line)
python main.py add --file parts.txt

# Generate a print PDF for specific parts
python main.py print NE555 BC547 LM358 --start 3 --output my_labels.pdf

# Force refresh data for a part (bypass cache)
python main.py add NE555 --refresh

# Override pinout image manually
python main.py set-image NE555 --pinout path/to/image.png

# List all cached components
python main.py list

# Launch web UI
python main.py web
```

---

## Web UI (`app.py`)

Built with **Flask** + plain HTML/CSS/JS (no heavy frontend framework needed).

Pages:
- `/` — Dashboard: cached components in two sections — **Needs review** (amber, at
  the top, with each part's review reason + an *Edit & approve* button) and
  **Ready** below; search/filter by type
- `/component/<mpn>` — Detail view: specs, pinout image, package image, label
  preview, and a review banner while the part is `needs_review`
- `/component/<mpn>/edit` — Edit label: swap images, override specs, change
  template; shows an **Approve** button (save + `status → ready`) while
  `needs_review`
- `/print` — Print job builder: select components, set start position, download PDF
- `/generic/<key>/print` — Generic-group print flow (GET form / POST → PDF): pick
  a value for each parameter, tick catalogue values, generate one label per value
  (see **Generic Component System**). Parameter-less groups (diodes) skip straight
  to the value list.
- `/generic/<key>/preview` — single-label HTML for that page's live preview iframe
  (`?i=<value index>` + `param_<key>=…`); mirrors `/label/<mpn>` for cached parts
- `/settings` — Sheet geometry tweaks, colour map editor, API credentials

---

## API Integration — Nexar (Octopart)

- Auth endpoint: `https://identity.nexar.com/connect/token` (OAuth2 client_credentials)
- GraphQL endpoint: `https://api.nexar.com/graphql`
- Credentials stored in `.env` file, loaded via `python-dotenv`
- Token is short-lived; re-fetch if expired (cache token + expiry in memory)
- Free tier sufficient for personal use; not suitable for thousands of queries

The query in `core/lookup.py` fetches: MPN, manufacturer, description, specs
array, bestDatasheet URL, documentCollections. This can be expanded.

### Provider interface (swappability contract)

Nexar is not hardwired into the system. The rest of the codebase depends on
`core/lookup.py` only through a **two-function interface**, so an alternative
data provider (a different API, a local DB, a CSV importer) can be dropped in
by replacing `core/lookup.py` alone — no other module changes:

- `search_component(part_number: str, limit: int = 3) -> dict | None`
  Returns one **component record dict** (shape below) for the best match, or
  `None` if nothing is found. Authentication is handled internally (callers
  never pass a token). Hard failures (bad query, quota, unreachable provider)
  raise `RuntimeError`; missing credentials raise `NexarAuthError`.
- `find_datasheet_url(part: dict) -> str | None`
  Given a record dict, returns the best datasheet URL (or `None`).

Everything else (`main.py`, `core/label_builder.py`, the web UI) is written
against the **record dict — that dict shape is the real contract**, not the
Nexar API. A replacement provider must map its native response into this
shape:

```python
{
    "mpn":              str,            # canonical part number; cached as the key
    "manufacturer":     {"name": str} | None,   # only .name is read
    "shortDescription": str | None,     # -> components.description
    "specs": [                          # stored verbatim as specs_json
        {                               # label_builder matches on the inner keys,
            "attribute": {"name": str, "shortname": str},  # so both must be present
            "displayValue": str,        # the human-readable value shown on labels
        },
        # ...
    ],
    "bestDatasheet":       {"url": str} | None,  # preferred datasheet source
    "documentCollections": [            # fallback scanned by find_datasheet_url
        {"documents": [{"url": str, "name": str}, # for the first .pdf link
        # ...
        ]},
        # ...
    ],
}
```

Notes that keep a replacement honest:
- All consumers read fields defensively (`(part.get("manufacturer") or {})...`),
  so any field except `mpn` may be absent or `None`; a provider can omit
  `documentCollections` if `bestDatasheet` is always supplied.
- The `specs` element shape is load-bearing: `core/label_builder.py` resolves
  per-type key specs by substring-matching `config.LABEL_SPECS` terms against
  the normalised `attribute.shortname` + `attribute.name`, then displays
  `displayValue`. A provider with differently-named attributes still works as
  long as it emits this `{attribute:{name,shortname}, displayValue}` triple.
- `component_type` is **not** part of this contract — no provider field maps to
  it. It is supplied out of band (CLI `--type`, or the web UI) and defaults to
  the generic template when absent.

---

## Package Diagrams & the Review Workflow

There is **no datasheet image extraction**. Datasheets are still downloaded (for
the cached-PDF link and to back the QR code), but every diagram on a label comes
from a curated, bundled package-outline SVG library — never from rasterising a PDF.

**Automatic source — `core/label_builder.py`:**
- `resolve_package_image(record)` maps the part's package/case spec (e.g.
  "TO-92-3", "SOIC-8") to an SVG in `templates/packages/` via `config.PACKAGE_MAP`
  (longest alias wins, so "sot235" → SOT-23-5 not SOT-23)
- `package_image_path(record)` returns the effective package image: a manually
  set `package_image_path` wins, otherwise the resolved outline (pin names
  relabelled per component type via `config.PACKAGE_PIN_NAMES`)
- `package_review_status(record)` decides the status the pipeline writes:
  - SVG matched → `('ready', None)`
  - package spec present but unmatched → `('needs_review', "No package diagram
    found for '<package>'")`
  - no package spec at all → `('needs_review', "Package not identified")`

**Review / approval flow:**
- The dashboard splits cached parts into a **Needs review** section (amber, at the
  top, showing each part's `review_reason` and an *Edit & approve* button) and a
  **Ready** section below
- The web editor shows an **Approve** button only while the part is
  `needs_review`. Approving saves the current editor state *and* flips
  `status → 'ready'`, clearing `review_reason`. The intended fix is to pick a
  package image (or upload one) and then approve
- To add support for a missing package instead of approving one part at a time:
  drop an SVG into `templates/packages/` and add its aliases to
  `config.PACKAGE_MAP` — re-adding affected parts then resolves them to `ready`

**Manual override** (unchanged): the user can supply their own pinout or package
image via the CLI (`set-image`) or the web editor. `pinout_image_path` is now a
manual-only column — the pipeline never writes it.

---

## Generic Component System

Generic groups are pre-defined component families (resistor / capacitor / diode
series) that have **no specific MPN and need no API lookup**. They are seeded
into the `generic_groups` table on first run and appear on the dashboard mixed
in with real cached components. Instead of a detail page, a generic group has a
**print flow**: the user picks parameter value(s) and ticks the catalogue values
they want, and one label is generated **on the fly** per ticked value — nothing
is stored in the DB per value.

**Catalogue data — `core/generics.py`** (pure data; imports nothing from the
project, so `cache.py` can seed from it without a circular import):
- E-series tables (E6/E12/E24/E96) + `format_resistance()` / `format_capacitance()`
- `seed_definitions()` → the full list of group definitions. Seeded groups:
  - **Resistors:** Metal Film 1% (E96, 1Ω–10MΩ; Power param; Tolerance 1% fixed),
    Carbon Film 5% (E24, 1Ω–10MΩ; Power param), Wirewound (E24, 1Ω–100kΩ; Power param)
  - **Electrolytic caps:** GP 85 °C, HT 105 °C (E6, 1µF–10000µF; Voltage param)
  - **Ceramic caps:** X7R, X5R (E12, 1pF–100µF; Voltage param)
  - **Diodes (fixed value lists, no parameters):** Rectifier 1N400x (V_RRM/I_F per
    part), Zener 1N4728A–1N4764A (V_Z per part, 1 W), Schottky 1N581x (V_R/I_F)

**Label generation — `core/label_builder.build_generic_label(group, value, params)`**
is the on-the-fly counterpart to `build_label(mpn)`; it builds the same context
(reusing `resistor_band_svg()`, colour + luminance logic) but from a group +
value + picked params instead of a cached record, and shows no MPN/QR/images:
- **Resistor:** headline = value (e.g. "10 kΩ") with the colour-band SVG; Power
  (param) + Tolerance (fixed) as specs — same as a real resistor label, no MPN
- **Capacitor:** headline = capacitance + selected voltage ("100 µF 50 V");
  dielectric/temp/tolerance (fixed) as specs; no MPN
- **Diode:** the part number **is** the value (headline, e.g. "1N4007"); the
  per-part voltage/current ratings are the specs

A parameter's `role` decides how the picked option is applied: `"spec"` (a spec
row) or `"headline"` (appended to the headline). New diode families that need
their own dashboard chip/colour get a component type in `config._TYPE_TABLE`
(this is why `rectifier_diode` and `schottky_diode` were added).

**PDF rendering** reuses `core/pdf_renderer.py`: the layout/render core was split
into MPN-agnostic `build_sheet_html_from_labels(labels, …)` / `render_labels(
labels, …)` (taking `[{"html", "colour"}]`), with `build_sheet_html(mpns,…)` /
`render_sheet(mpns,…)` now thin wrappers. The generic print POST builds its
labels with `build_generic_label()` and calls `render_labels()` directly.

**Dashboard integration:** generic groups are merged into the **Ready** section,
sorted in with real parts. They are visually distinct — a violet row tint, a
`GENERIC` badge in place of the type colour dot, and a *Print values…* action
instead of a detail link — and they are included in the type filter-chip counts.

## Dependencies

```
requests
pymupdf          # fitz — legacy component_lookup.py prototype only (no longer
                 # used by the active pipeline; safe to drop once that's deleted)
weasyprint       # HTML/CSS → PDF rendering
flask            # web UI
python-dotenv    # .env credential loading
qrcode[pil]      # QR code generation for datasheet URLs
Pillow           # image handling
jinja2           # label templating (also a Flask dependency)
```

---

## What Already Exists

`component_lookup.py` — the original prototype, now superseded by the `core/`
modules below. Kept only as reference until `main.py` replaces its `run()`
orchestrator; do not extend it. (Its hardcoded Nexar credentials must be moved
to `.env` and rotated.)

Implemented modules — signatures that differ from the prototype:

- `core/cache.py` — full SQLite layer: `init_db()`, `upsert_component(mpn, **fields)`
  (COALESCE upsert: None fields preserve existing values, so it doubles as a
  partial-update helper; `status`/`review_reason` are a coupled pair — see the
  schema notes), `get_component`, `component_exists`, `list_components`,
  `save_label`, `get_label`, `get_labels`, `get_latest_label`. All file paths
  stored project-root-relative.
- `core/lookup.py` — `get_access_token(force_refresh=False)` caches the OAuth
  token + expiry in memory; `search_component(part_number, limit=3)` **no longer
  takes a token argument** (auth handled internally; 401 retried once with a
  fresh token); GraphQL-level errors raise RuntimeError instead of returning
  None. `find_datasheet_url(part)` and `print_specs(part, max_specs=15)` as before.
- `core/datasheet.py` — download-only now. `download_pdf(url, mpn, *, force=False,
  dest_path=None) -> Path | None` (**was** `(url, dest_path) -> bool`): skips the
  download when the file is already cached unless `force`, validates PDF magic
  bytes, writes atomically. `datasheet_path_for(mpn)` returns the canonical cache
  path. The pinout extraction/scoring functions (`extract_pinout_pages`,
  `score_page`, `_rank_candidate_pages`) and the `fitz`/PyMuPDF dependency have
  been removed from this module.
- `core/label_builder.py` — `build_label(mpn, overrides=None) -> str` renders a
  cached component to label HTML: template chosen via `config.type_template()`,
  key specs via `config.LABEL_SPECS`, QR code generated to the component's
  web-UI page, bar text colour picked by luminance. `overrides` lets the web UI
  substitute any context variable (special key `"template"` swaps the template).
  Also owns package resolution: `resolve_package_image()`, `package_image_path()`,
  and `package_review_status(record) -> (status, review_reason)` (the pipeline's
  ready/needs_review decision).
- `config.py` — paths, Nexar credentials (env only), sheet geometry, component
  type table (display name + colour + template), `LABEL_SPECS` key-spec table,
  `QR_HOST`.
- `templates/` — all three label templates + shared `label_styles.css` +
  `preview.html`.

---

## Priorities / Build Order

1. ✅ `core/cache.py` — SQLite schema + read/write helpers (foundation everything else uses)
2. ✅ `core/lookup.py` — refactored from `component_lookup.py`
3. ✅ `core/datasheet.py` — refactored from `component_lookup.py`
4. ✅ `config.py` — sheet geometry + colour map (built alongside step 1)
5. ✅ `templates/` — HTML label templates (simple + complex + connector)
6. ✅ `core/label_builder.py` — populates templates with data
7. `core/pdf_renderer.py` — A4 sheet grid renderer via WeasyPrint
8. `main.py` — CLI wiring (also replaces `component_lookup.py`'s `run()`)
9. `app.py` + `web/` — Flask web UI

---

## Notes & Constraints

- All file paths in the DB should be stored relative to the project root so the
  project folder can be moved without breaking references — never use absolute paths
- All configuration (credentials, paths, port, host) must be driven by environment
  variables or `.env` file — never hardcoded — so the app works identically on bare
  metal, in a Proxmox VM, or inside a Docker container
- The system must be easy to extend: adding a new component type means adding one
  entry to the colour map in `config.py` and optionally a new label template
- Label HTML templates must render correctly at exactly 45mm × 45mm — WeasyPrint
  supports physical units (mm) in CSS so use those, not pixels
- WeasyPrint on Linux: `apt install weasyprint` or `pip install weasyprint` — no
  additional runtime dependencies needed unlike Windows
- QR codes link to the component's page on the local web UI —
  `http://<QR_HOST>/component/<mpn>` (`QR_HOST` from env, e.g. a Tailscale name) —
  generated at label-build time. The component page links the datasheet, so the
  QR stays short, scannable at 7mm, and valid even if the vendor URL changes
- Flask should bind to `0.0.0.0` (not `127.0.0.1`) so it is accessible over the
  local network and via Tailscale — port configurable via env var, default 5000

## Docker Readiness (future)

The project structure is designed so a `Dockerfile` and `docker-compose.yml` can be
added later with no refactoring. When that time comes:
- `db/` and `output/` should be volume mounts so data persists across container restarts
- All secrets come from environment variables (already the case via `.env`)
- WeasyPrint and all apt dependencies should be captured in the Dockerfile
- Example `docker-compose.yml` volumes:
  ```yaml
  volumes:
    - ./db:/app/db
    - ./output:/app/output
  ```

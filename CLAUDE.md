# Component Label System — Project Brief for Claude Code

## Project Overview

A fully automated electronic component label generation system. The user supplies one
or more part numbers via CLI or web UI; the system looks up component data, extracts
pinout diagrams and package images from datasheets, generates print-ready PDF labels
formatted for Avery 45×45-S sheets, and stores everything locally so no repeat API
calls are needed.

The goal is maximum automation: give it a part number, get a print-ready label. The
only manual steps should be optional overrides (e.g. swapping a pinout image).

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
│   ├── datasheet.py           ← PDF download, pinout page scoring, image extraction
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
    ├── datasheets/            ← Cached downloaded PDFs
    ├── images/                ← Extracted pinout + package images
    └── labels/                ← Generated PDF label sheets
```

---

## Data Pipeline (per part number)

1. **Cache check** — query SQLite for existing record; if found, skip to step 5
2. **API lookup** — authenticate with Nexar OAuth2, run GraphQL query for part data
3. **Datasheet download** — fetch PDF to `output/datasheets/`
4. **Image extraction:**
   - Score all PDF pages by pinout keyword density + embedded image count
   - Rasterize top 3 candidate pages to `output/images/<MPN>/pinout_candidate_N.png`
   - Also attempt to extract a package/physical photo if present in the PDF
   - Store best candidate as the default; allow manual override via web UI
5. **Cache write** — store all data + image paths to SQLite
6. **Label build** — select template based on component type, populate with data
7. **PDF render** — place label(s) onto A4 sheet at correct grid position(s)

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
| pinout_image_path | TEXT | auto-extracted or manual override |
| package_image_path | TEXT | physical package photo if available |
| created_at | TEXT | ISO timestamp |
| updated_at | TEXT | ISO timestamp |

### `labels` table
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| mpn | TEXT | FK to components.mpn |
| label_html | TEXT | rendered HTML of the label |
| label_pdf_path | TEXT | path to generated single-label PDF |
| created_at | TEXT | |

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
- `/` — Dashboard: list of all cached components, search/filter by type
- `/component/<mpn>` — Detail view: specs, pinout image, package image, label preview
- `/component/<mpn>/edit` — Edit label: swap images, override specs, change template
- `/print` — Print job builder: select components, set start position, download PDF
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

## Pinout Image Extraction — Current Approach

Implemented in `core/datasheet.py`:
- Download datasheet PDF (cached: re-download only with `force=True`; response
  is validated as a real PDF and written atomically)
- Score each page: keyword frequency (pin, pinout, diagram, etc.) × weights +
  embedded image count × 1.5
- **Vector-pinout fallback:** if no page in the PDF has any embedded raster
  image, pages are ranked by keyword density alone; zero-scored pages are never
  returned (fewer than 3 candidates — or none, with a warning — instead of junk)
- Rasterize top 3 candidates at 144 DPI (2× fitz.Matrix) →
  `output/images/<MPN>/pinout_candidate_<rank>_p<page>.png`
- Store top candidate as default pinout image
- Possible future upgrade: pass rasterised pages to a vision model when the
  keyword heuristic also fails

Manual override: user can supply their own image via CLI or web UI. This replaces
the auto-extracted image in the DB without losing the original.

---

## Dependencies

```
requests
pymupdf          # fitz — PDF parsing and rasterization
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
  partial-update helper), `get_component`, `component_exists`, `list_components`,
  `save_label`, `get_label`, `get_labels`, `get_latest_label`. All file paths
  stored project-root-relative.
- `core/lookup.py` — `get_access_token(force_refresh=False)` caches the OAuth
  token + expiry in memory; `search_component(part_number, limit=3)` **no longer
  takes a token argument** (auth handled internally; 401 retried once with a
  fresh token); GraphQL-level errors raise RuntimeError instead of returning
  None. `find_datasheet_url(part)` and `print_specs(part, max_specs=15)` as before.
- `core/datasheet.py` — `download_pdf(url, mpn, *, force=False, dest_path=None)
  -> Path | None` (**was** `(url, dest_path) -> bool`): skips the download when
  the file is already cached unless `force`, validates PDF magic bytes, writes
  atomically. `datasheet_path_for(mpn)` returns the canonical cache path.
  `extract_pinout_pages(pdf_path, mpn=None, *, output_dir=None, top_n=3)`
  defaults output to `output/images/<MPN>/`. `score_page(page, count_images=True)`.
- `core/label_builder.py` — `build_label(mpn, overrides=None) -> str` renders a
  cached component to label HTML: template chosen via `config.type_template()`,
  key specs via `config.LABEL_SPECS`, QR code generated to the component's
  web-UI page, bar text colour picked by luminance. `overrides` lets the web UI
  substitute any context variable (special key `"template"` swaps the template).
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

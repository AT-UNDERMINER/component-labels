"""
app.py — Flask web UI for the Component Label System (build step 9).

Thin presentation layer over the existing core/ modules — it owns no business
logic of its own:

    cache         read components + labels, persist edits
    label_builder render a component to label HTML (for the inline preview)
    pdf_renderer  render selected components to a downloadable A4 PDF
    config        sheet geometry, colour map, credential status, settings file

Pages (per CLAUDE.md > Web UI):
    /                       dashboard, filterable by type
    /component/<mpn>        detail: specs, images, datasheet, label preview
    /component/<mpn>/edit   edit: swap images, override specs, change type
    /print                  job builder -> downloadable PDF via render_sheet()
    /settings               geometry + colour editor, API credential status

Asset-serving helpers (so the inline label preview resolves its relative URLs):
    /label/<mpn>            raw build_label() HTML with a <base> injected
    /labelsrc/<path>        serves templates/ (label_styles.css)
    /output/<path>          serves output/ (pinout / package / qr images, PDFs)

Run with `python main.py web` (which calls main() below) or `python app.py`.
Binds 0.0.0.0 so it is reachable over the LAN / Tailscale; host/port/secret come
from the environment. WeasyPrint is only needed for /print; every other page
works without it, and /print degrades to a clean flash message if it is missing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

import config
from core import cache, label_builder, pdf_renderer
from core.datasheet import _safe_filename

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
)
# Flash messages need a session secret; a dev default is fine for a LAN tool.
app.secret_key = os.getenv("SECRET_KEY", "dev-component-labels")


# ── Template helpers (available in every template via context processor) ───────

def _output_url(stored_path: str | Path | None) -> str | None:
    """Map a stored file path to its /output/<...> URL, or None if not under
    OUTPUT_DIR. Stored paths are project-root-relative (see cache._relativize),
    but this resolves them robustly however OUTPUT_DIR is configured."""
    if not stored_path:
        return None
    p = Path(stored_path)
    abs_p = p if p.is_absolute() else (config.PROJECT_ROOT / p)
    try:
        rel = abs_p.resolve().relative_to(config.OUTPUT_DIR.resolve())
    except (ValueError, OSError):
        return None
    return "/output/" + rel.as_posix()


@app.context_processor
def _inject_helpers() -> dict:
    return {
        "type_display_name": config.type_display_name,
        "type_colour": config.type_colour,
        "bar_text": label_builder.bar_text_colour,
        "component_types": config.COMPONENT_TYPES,
        "output_url": _output_url,
        "per_sheet": config.LABELS_PER_SHEET,
    }


def _pinout_candidates(mpn: str) -> list[dict]:
    """Auto-extracted pinout candidates for a part, for the edit page picker."""
    folder = config.IMAGE_DIR / _safe_filename(mpn)
    if not folder.is_dir():
        return []
    out = []
    for png in sorted(folder.glob("pinout_candidate_*.png")):
        rel = png.resolve().relative_to(config.PROJECT_ROOT).as_posix()
        out.append({"value": rel, "url": _output_url(rel), "name": png.name})
    return out


def _rebuild_label(mpn: str) -> None:
    """Re-render and store the label HTML after an edit (mirrors add step 6)."""
    cache.save_label(mpn, label_html=label_builder.build_label(mpn))


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    type_filter = request.args.get("type") or None

    all_components = cache.list_components()
    counts: dict = {}
    for c in all_components:
        counts[c.get("component_type")] = counts.get(c.get("component_type"), 0) + 1

    if type_filter == "__none__":
        components = [c for c in all_components if not c.get("component_type")]
    elif type_filter:
        components = cache.list_components(type_filter)
    else:
        components = all_components

    return render_template(
        "dashboard.html",
        components=components,
        counts=counts,
        total=len(all_components),
        type_filter=type_filter,
    )


# ── Component detail ─────────────────────────────────────────────────────────────

@app.route("/component/<mpn>")
def component_detail(mpn: str):
    record = cache.get_component(mpn)
    if record is None:
        abort(404)
    return render_template("detail.html", c=record, specs=record.get("specs") or [])


# ── Component edit ────────────────────────────────────────────────────────────────

@app.route("/component/<mpn>/edit", methods=["GET", "POST"])
def component_edit(mpn: str):
    record = cache.get_component(mpn)
    if record is None:
        abort(404)

    if request.method == "POST":
        form = request.form

        # Raw specs JSON (advanced). Empty -> no change; invalid -> re-render.
        specs_text = (form.get("specs_json") or "").strip()
        specs_val = None
        if specs_text:
            try:
                specs_val = json.loads(specs_text)
            except ValueError as exc:
                flash(f"Specs JSON is invalid, nothing saved: {exc}", "error")
                return render_template(
                    "edit.html", c=record,
                    pinout_candidates=_pinout_candidates(mpn),
                    specs_json=specs_text,
                )

        pinout_val = _resolve_image_choice(
            form.get("pinout_choice"), form.get("pinout_custom"))
        package_val = _resolve_image_choice(
            form.get("package_choice"), form.get("package_custom"))

        cache.upsert_component(
            mpn,
            manufacturer=form.get("manufacturer"),
            component_type=form.get("component_type"),
            description=form.get("description"),
            specs=specs_val,
            pinout_image_path=pinout_val,
            package_image_path=package_val,
        )
        _rebuild_label(mpn)
        flash(f"Saved '{mpn}' and rebuilt its label.", "success")
        return redirect(url_for("component_detail", mpn=mpn))

    specs_json = json.dumps(record.get("specs") or [], indent=2, ensure_ascii=False)
    return render_template(
        "edit.html", c=record,
        pinout_candidates=_pinout_candidates(mpn),
        specs_json=specs_json,
    )


def _resolve_image_choice(choice: str | None, custom: str | None) -> str | None:
    """Translate an edit-form image radio into a path for upsert (None = keep).

    "keep" -> None (COALESCE preserves the existing value); "custom" -> the
    typed path (or None if blank); anything else is a chosen candidate path.
    """
    if not choice or choice == "keep":
        return None
    if choice == "custom":
        custom = (custom or "").strip()
        return custom or None
    return choice


# ── Print job builder ────────────────────────────────────────────────────────────

@app.route("/print", methods=["GET", "POST"])
def print_builder():
    if request.method == "POST":
        mpns = request.form.getlist("mpns")
        if not mpns:
            flash("Select at least one component to print.", "error")
            return redirect(url_for("print_builder"))

        try:
            start = int(request.form.get("start", 1))
        except (TypeError, ValueError):
            flash("Start position must be a whole number.", "error")
            return redirect(url_for("print_builder"))
        if not (1 <= start <= config.LABELS_PER_SHEET):
            flash(f"Start position must be 1–{config.LABELS_PER_SHEET}.", "error")
            return redirect(url_for("print_builder"))

        missing = [m for m in mpns if not cache.component_exists(m)]
        if missing:
            flash("Not cached: " + ", ".join(missing), "error")
            return redirect(url_for("print_builder"))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = config.LABEL_DIR / f"labels_{stamp}.pdf"
        try:
            pdf_renderer.render_sheet(mpns, out, start=start)
        except RuntimeError as exc:           # WeasyPrint missing, etc.
            flash(str(exc), "error")
            return redirect(url_for("print_builder"))
        except Exception as exc:              # any render failure -> clean message
            flash(f"PDF generation failed: {exc}", "error")
            return redirect(url_for("print_builder"))

        return send_file(
            out, as_attachment=True, download_name=out.name,
            mimetype="application/pdf",
        )

    return render_template("print.html", components=cache.list_components())


# ── Settings ──────────────────────────────────────────────────────────────────────

_GEOMETRY_FIELDS = [
    ("SHEET_WIDTH_MM", "Sheet width", "0.5", False),
    ("SHEET_HEIGHT_MM", "Sheet height", "0.5", False),
    ("LABEL_WIDTH_MM", "Label width", "0.1", False),
    ("LABEL_HEIGHT_MM", "Label height", "0.1", False),
    ("GRID_COLS", "Columns", "1", True),
    ("GRID_ROWS", "Rows", "1", True),
    ("MARGIN_TOP_MM", "Top margin", "0.1", False),
    ("MARGIN_LEFT_MM", "Left margin", "0.1", False),
    ("GAP_H_MM", "Horizontal gap", "0.1", False),
    ("GAP_V_MM", "Vertical gap", "0.1", False),
]


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        geometry: dict = {}
        for name, _label, _step, is_int in _GEOMETRY_FIELDS:
            raw = (request.form.get(f"geo_{name}") or "").strip()
            if not raw:
                continue
            try:
                geometry[name] = int(raw) if is_int else float(raw)
            except ValueError:
                flash(f"Ignored invalid value for {name}: {raw!r}", "error")

        colours: dict = {
            key: request.form.get(f"col_{key}")
            for key in config.COMPONENT_TYPES
            if request.form.get(f"col_{key}")
        }

        try:
            config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            config.SETTINGS_PATH.write_text(
                json.dumps({"geometry": geometry, "colours": colours}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            flash(f"Could not write settings: {exc}", "error")
            return redirect(url_for("settings"))

        flash("Settings saved to db/settings.json. Restart the server to apply.",
              "success")
        return redirect(url_for("settings"))

    geometry_fields = [
        {"name": name, "label": label, "step": step,
         "value": getattr(config, name)}
        for name, label, step, _is_int in _GEOMETRY_FIELDS
    ]
    api = {
        "client_id": bool(config.NEXAR_CLIENT_ID),
        "client_secret": bool(config.NEXAR_CLIENT_SECRET),
        "token_url": config.NEXAR_TOKEN_URL,
        "api_url": config.NEXAR_API_URL,
        "qr_host": config.QR_HOST,
    }
    return render_template("settings.html", geometry_fields=geometry_fields, api=api)


# ── Asset-serving routes (for the inline label preview + cached images) ─────────

@app.route("/label/<mpn>")
def label_html(mpn: str):
    """Raw single-label HTML for the preview iframe.

    Two URL-resolution concerns, handled independently so the preview is robust
    to any OUTPUT_DIR layout:
      • the stylesheet link is relative to templates/ — a <base href="/labelsrc/">
        makes the browser fetch it from the labelsrc route;
      • the image URLs are overridden to explicit /output/... URLs (root-relative,
        so <base> doesn't touch them), mapped via _output_url from the stored
        paths. The QR is regenerated by build_label at a deterministic path.
    """
    record = cache.get_component(mpn)
    if record is None:
        abort(404)

    qr_path = config.IMAGE_DIR / _safe_filename(mpn) / "qr.png"
    html = label_builder.build_label(mpn, overrides={
        "pinout_image": _output_url(record.get("pinout_image_path")),
        "package_image": _output_url(record.get("package_image_path")),
        "qr_image": _output_url(qr_path),
    })
    return html.replace("<head>", '<head><base href="/labelsrc/">', 1)


@app.route("/labelsrc/<path:filename>")
def labelsrc(filename: str):
    return send_from_directory(config.TEMPLATE_DIR, filename)


@app.route("/output/<path:filename>")
def output_files(filename: str):
    return send_from_directory(config.OUTPUT_DIR, filename)


# ── Entry point (invoked by `python main.py web`) ──────────────────────────────

def main() -> None:
    """Start the dev server. Host/port/debug come from the environment."""
    cache.init_db()
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT") or os.getenv("PORT") or "5000")
    debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    print(f"Component Labels web UI -> http://{host}:{port}  (Ctrl-C to stop)")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()

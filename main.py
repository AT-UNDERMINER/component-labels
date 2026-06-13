"""
main.py — command-line entry point for the Component Label System.

This is the wiring layer (build step 8): it parses CLI arguments with argparse
and drives the already-built core modules (cache, lookup, datasheet,
label_builder, pdf_renderer) through the data pipeline described in CLAUDE.md.
It deliberately contains no business logic of its own — every command is a thin
handler around the core API.

Commands:
    add <MPN> [--type T] [--refresh]   look up a part, cache it, build its label
    add --file <path> [--type T] ...   batch version: one MPN per line
    print <MPN>... [--start N] [-o P]   render a print-ready A4 label sheet
    set-image <MPN> --pinout <path>     override a part's pinout image in the DB
    list                                tabulate every cached component
    web                                 launch the Flask web UI (app.py, step 9)

Design choices:
  • The `add` pipeline lives in ONE orchestrator, add_component(), which the
    single-part and batch commands both call — there is no duplicated pipeline.
  • Heavy/optional dependencies (PyMuPDF, Jinja2, WeasyPrint, requests) are
    imported lazily inside the handlers that need them, so `--help` and `list`
    work even on a machine that only has the SQLite/stdlib stack installed.
  • No raw tracebacks ever reach the user: main() funnels every exception into a
    clean one-line message and a non-zero exit code. Set CLI_DEBUG=1 to re-raise
    and see the full traceback while developing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import config
from core import cache  # cache only needs config + the stdlib — safe to import early


# ── Clean error type ──────────────────────────────────────────────────────────

class CommandError(Exception):
    """A user-facing error. main() prints its message and exits non-zero.

    Raise this (rather than letting an arbitrary exception escape) whenever the
    failure is something the user can understand and act on — a missing file,
    an uncached part, a part the API doesn't know.
    """


# ── Small console helpers (ASCII only, so output is safe on a cp1252 terminal) ─

def _step(n: int, total: int, msg: str) -> None:
    """Print a pipeline stage header, e.g. '[2/6] Looking up NE555...'."""
    print(f"[{n}/{total}] {msg}...")


def _ok(msg: str) -> None:
    print(f"  + {msg}")


def _warn(msg: str) -> None:
    print(f"  [!] {msg}")


# ── The add pipeline — single orchestrator ─────────────────────────────────────

def add_component(
    mpn: str,
    *,
    component_type: str | None = None,
    refresh: bool = False,
) -> str:
    """Run the full per-part pipeline (CLAUDE.md "Data Pipeline") for one MPN.

        1. Cache check    — if cached (and not --refresh), skip straight to the
                            label build using the stored data.
        2. API lookup     — Nexar GraphQL search for the part record.
        3. Datasheet      — download the PDF (cache-aware, atomic).
        4. Image extract  — score pages, rasterize the best pinout candidates.
        5. Cache write    — persist the record + image paths to SQLite.
        6. Label build    — render the label HTML and save it.

    Both the single-part `add` command and the batch `add --file` command call
    this one function — the batch path is just a loop around it, so there is a
    single source of truth for the pipeline.

    Returns the canonical (API-resolved) MPN the data was cached under. Raises
    CommandError for failures the user should see (e.g. no search results); a
    missing datasheet or pinout is treated as a non-fatal warning so a usable
    label is still produced from the specs alone.
    """
    # Lazy imports: these pull in requests / PyMuPDF, which we don't want to
    # require for `list` or `--help`.
    from core import datasheet, lookup

    print(f"\n=== {mpn} ===")

    # ── 1. Cache check ──────────────────────────────────────────────────────
    _step(1, 6, f"Cache check for '{mpn}'")
    if not refresh and cache.component_exists(mpn):
        _ok(f"'{mpn}' is already cached - skipping API lookup "
            "(use --refresh to re-fetch).")
        _step(6, 6, "Rebuilding label from cached data")
        _build_and_save_label(mpn)
        _ok(f"Label ready for '{mpn}'.")
        return mpn
    if refresh:
        print("  --refresh: bypassing the cache, re-fetching everything.")

    # ── 2. API lookup ───────────────────────────────────────────────────────
    _step(2, 6, f"Looking up '{mpn}' via Nexar")
    part = lookup.search_component(mpn)
    if part is None:
        raise CommandError(f"No results found for '{mpn}'.")

    resolved = part.get("mpn") or mpn
    if resolved != mpn:
        print(f"  Resolved '{mpn}' -> '{resolved}' "
              "(caching under the canonical MPN).")
    manufacturer = (part.get("manufacturer") or {}).get("name")
    description = part.get("shortDescription")
    specs = part.get("specs") or []
    print(f"  {resolved}  |  {manufacturer or 'unknown manufacturer'}")
    print(f"  {description or 'no description'}")
    print(f"  {len(specs)} spec attribute(s) returned.")

    # Type resolution: an explicit --type always wins; otherwise auto-classify
    # from the part record. A None result (passed through to the COALESCE
    # upsert) preserves any type already on the record and leaves new parts on
    # the default template.
    if component_type is not None:
        effective_type = component_type
        print(f"  Type: '{effective_type}' (from --type).")
    else:
        effective_type = lookup.classify_component_type(part)
        if effective_type:
            print(f"  Type: auto-classified as '{effective_type}' "
                  f"({config.type_display_name(effective_type)}); override with --type.")
        else:
            _warn("Could not auto-classify the type - using the default "
                  "template. Set one with --type.")

    # ── 3. Datasheet download ───────────────────────────────────────────────
    _step(3, 6, "Datasheet download")
    ds_url = lookup.find_datasheet_url(part)
    pdf_path: Path | None = None
    if not ds_url:
        _warn("No datasheet URL for this part - label will have no pinout image.")
    else:
        print(f"  URL: {ds_url}")
        pdf_path = datasheet.download_pdf(ds_url, resolved, force=refresh)
        if pdf_path is None:
            _warn("Datasheet download failed - continuing without a pinout image.")

    # ── 4. Image extraction ─────────────────────────────────────────────────
    _step(4, 6, "Pinout image extraction")
    pinout_path: Path | None = None
    if pdf_path is not None:
        candidates = datasheet.extract_pinout_pages(pdf_path, resolved)
        if candidates:
            pinout_path = candidates[0]
            _ok(f"{len(candidates)} candidate(s); using {pinout_path.name} "
                "as the default pinout.")
        else:
            _warn("No pinout candidates extracted - use `set-image` to add one.")
    else:
        print("  Skipped (no datasheet to extract from).")

    # ── 5. Cache write ──────────────────────────────────────────────────────
    _step(5, 6, "Writing to cache")
    cache.upsert_component(
        resolved,
        manufacturer=manufacturer,
        component_type=effective_type,
        description=description,
        specs=specs,
        datasheet_url=ds_url,
        datasheet_path=pdf_path,
        pinout_image_path=pinout_path,
    )
    _ok(f"Cached '{resolved}'.")

    # ── 6. Label build ──────────────────────────────────────────────────────
    _step(6, 6, "Building label")
    _build_and_save_label(resolved)
    _ok(f"Label ready for '{resolved}'.")
    return resolved


def _build_and_save_label(mpn: str) -> str:
    """Render a cached part's label HTML and record it in the labels table.

    Shared by the add pipeline (step 6) and the set-image command, so a manual
    image override takes effect in the stored label immediately.
    """
    from core import label_builder

    html = label_builder.build_label(mpn)
    cache.save_label(mpn, label_html=html)
    print(f"  Built label HTML ({len(html)} chars) and saved it to the DB.")
    return html


# ── Command handlers ────────────────────────────────────────────────────────

def cmd_add(args: argparse.Namespace) -> int:
    """`add` — single part or `--file` batch. Wraps add_component()."""
    if args.file and args.mpn:
        raise CommandError("Give either an MPN or --file, not both.")
    if not args.file and not args.mpn:
        raise CommandError("Nothing to add. Give an MPN, or --file <path> "
                           "for a batch.")

    cache.init_db()

    # Single part: let any failure propagate to main() for a clean exit.
    if args.mpn:
        add_component(args.mpn, component_type=args.type, refresh=args.refresh)
        return 0

    # Batch: one MPN per line. A per-part CommandError is logged and skipped so
    # one bad part doesn't abort the run; other exceptions (e.g. an auth error
    # that will hit every part) still propagate and stop the batch.
    mpns = _read_mpn_file(args.file)
    if not mpns:
        raise CommandError(f"No MPNs found in {args.file}.")

    print(f"Batch add: {len(mpns)} part(s) from {args.file}")
    failures: list[str] = []
    for i, mpn in enumerate(mpns, start=1):
        print(f"\n----- [{i}/{len(mpns)}] -----")
        try:
            add_component(mpn, component_type=args.type, refresh=args.refresh)
        except CommandError as exc:
            _warn(f"Skipping '{mpn}': {exc}")
            failures.append(mpn)

    done = len(mpns) - len(failures)
    print(f"\nBatch complete: {done} succeeded, {len(failures)} failed.")
    if failures:
        print(f"  Failed: {', '.join(failures)}")
        return 1
    return 0


def cmd_print(args: argparse.Namespace) -> int:
    """`print` — render an A4 label sheet PDF for one or more cached parts."""
    cache.init_db()

    if not (1 <= args.start <= config.LABELS_PER_SHEET):
        raise CommandError(
            f"--start must be between 1 and {config.LABELS_PER_SHEET} "
            f"(got {args.start})."
        )

    missing = [m for m in args.mpns if not cache.component_exists(m)]
    if missing:
        raise CommandError(
            "These parts are not cached (run `add` first): "
            + ", ".join(missing)
        )

    output = Path(args.output) if args.output else (config.LABEL_DIR / "labels.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Building print sheet: {len(args.mpns)} label(s), "
          f"starting at position {args.start} of {config.LABELS_PER_SHEET}.")
    print(f"  Parts:  {', '.join(args.mpns)}")
    print(f"  Output: {output}")

    # core/pdf_renderer.py is build step 7 and may not exist yet. Import it
    # lazily and turn its absence into a clean message rather than a crash.
    # Expected interface (what step 7 must provide):
    #     render_sheet(mpns: list[str], output_path: Path, *, start: int = 1) -> Path
    try:
        from core import pdf_renderer
    except ImportError:
        raise CommandError(
            "PDF rendering is not available yet: core/pdf_renderer.py "
            "(build step 7) has not been implemented. Once it exists this "
            "command will render the sheet automatically."
        )
    if not hasattr(pdf_renderer, "render_sheet"):
        raise CommandError(
            "core/pdf_renderer.py exists but defines no render_sheet(); expected "
            "render_sheet(mpns, output_path, *, start=1) -> Path."
        )

    result = pdf_renderer.render_sheet(args.mpns, output, start=args.start)
    _ok(f"Print PDF written to {result}")
    return 0


def cmd_set_image(args: argparse.Namespace) -> int:
    """`set-image` — manual pinout override; updates the DB and rebuilds the label."""
    cache.init_db()

    image = Path(args.pinout)
    if not image.is_file():
        raise CommandError(f"Image file not found: {image}")
    if not cache.component_exists(args.mpn):
        raise CommandError(
            f"'{args.mpn}' is not cached - add it first "
            f"(main.py add {args.mpn})."
        )

    print(f"Overriding pinout image for '{args.mpn}' -> {image}")
    # upsert COALESCEs None fields, so this updates only pinout_image_path.
    cache.upsert_component(args.mpn, pinout_image_path=image)
    _ok("DB updated (pinout_image_path overridden).")

    print("Rebuilding the label with the new image...")
    _build_and_save_label(args.mpn)
    _ok(f"Label refreshed for '{args.mpn}'.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """`list` — print a table of every cached component."""
    cache.init_db()

    rows = cache.list_components()
    if not rows:
        print("No components cached yet. Add one with:  main.py add <MPN>")
        return 0

    headers = ("MPN", "Type", "Manufacturer")
    table = [
        (
            r["mpn"],
            config.type_display_name(r.get("component_type")),
            r.get("manufacturer") or "-",
        )
        for r in rows
    ]

    widths = [
        max(len(headers[col]), *(len(row[col]) for row in table))
        for col in range(len(headers))
    ]

    def fmt(cols: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(cols))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in table:
        print(fmt(row))
    print(f"\n{len(rows)} component(s) cached.")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """`web` — hand off to the Flask app in app.py (built in step 9)."""
    print("Launching the web UI...")
    try:
        import app
    except ImportError as exc:
        raise CommandError(f"Could not import app.py: {exc}")

    # The web UI itself lives entirely in app.py; we just invoke its entry
    # point. app.py is a stub until step 9, so fail cleanly if there is none.
    entry = getattr(app, "main", None) or getattr(app, "run", None)
    if entry is None or not callable(entry):
        raise CommandError(
            "The web UI is not implemented yet (build step 9: app.py)."
        )
    entry()
    return 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_mpn_file(path: str) -> list[str]:
    """Read a batch file: one MPN per line, blank lines and # comments ignored."""
    p = Path(path)
    if not p.is_file():
        raise CommandError(f"Batch file not found: {p}")
    mpns: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            mpns.append(line)
    return mpns


# ── Argument parsing ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI. Each subcommand wires func= to its handler."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Component Label System: look up electronic parts, cache "
                    "their data and datasheets, and generate print-ready "
                    "Avery 45x45-S label sheets.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # add ---------------------------------------------------------------------
    p_add = sub.add_parser(
        "add",
        help="Look up a part (or a batch), cache it, and build its label.",
    )
    p_add.add_argument("mpn", nargs="?", help="Manufacturer part number to add.")
    p_add.add_argument(
        "-f", "--file",
        help="Text file of MPNs (one per line) for a batch add.",
    )
    p_add.add_argument(
        "-t", "--type",
        choices=sorted(config.COMPONENT_TYPES),
        help="Component type - sets the label's colour, template and key specs. "
             "Optional: the Nexar API does not classify parts, so without this "
             "the default template is used.",
    )
    p_add.add_argument(
        "--refresh", action="store_true",
        help="Bypass the cache and re-fetch everything for the part.",
    )
    p_add.set_defaults(func=cmd_add)

    # print -------------------------------------------------------------------
    p_print = sub.add_parser(
        "print",
        help="Render a print-ready PDF label sheet for cached parts.",
    )
    p_print.add_argument(
        "mpns", nargs="+", metavar="MPN",
        help="One or more cached MPNs to place on the sheet.",
    )
    p_print.add_argument(
        "--start", type=int, default=1,
        help=f"First label position to fill, 1-{config.LABELS_PER_SHEET} "
             "(lets you reuse a partly-used sheet). Default: 1.",
    )
    p_print.add_argument(
        "-o", "--output",
        help="Output PDF path. Default: output/labels/labels.pdf",
    )
    p_print.set_defaults(func=cmd_print)

    # set-image ---------------------------------------------------------------
    p_img = sub.add_parser(
        "set-image",
        help="Override a cached part's pinout image and rebuild its label.",
    )
    p_img.add_argument("mpn", help="Cached MPN to update.")
    p_img.add_argument(
        "--pinout", required=True,
        help="Path to the replacement pinout image.",
    )
    p_img.set_defaults(func=cmd_set_image)

    # list --------------------------------------------------------------------
    p_list = sub.add_parser("list", help="List all cached components.")
    p_list.set_defaults(func=cmd_list)

    # web ---------------------------------------------------------------------
    p_web = sub.add_parser("web", help="Launch the Flask web UI (app.py).")
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch to the handler, and map errors to exit codes.

    Returns the process exit code. No exception is allowed to escape as a raw
    traceback — set CLI_DEBUG=1 to re-raise unexpected errors while developing.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 2

    try:
        return args.func(args) or 0
    except CommandError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, LookupError, OSError) as exc:
        # RuntimeError covers Nexar auth/GraphQL failures; LookupError covers an
        # uncached MPN reaching the label builder; OSError covers filesystem I/O.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:  # last-resort guard: never show a raw traceback
        if os.getenv("CLI_DEBUG"):
            raise
        print(f"Unexpected error: {exc}", file=sys.stderr)
        print("  (set CLI_DEBUG=1 for a full traceback)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

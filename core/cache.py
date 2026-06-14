"""
core/cache.py — SQLite read/write layer.

This is the foundation the rest of the pipeline sits on. It owns the database
schema and is the only module that talks to SQLite directly. Everything else
(lookup, datasheet, label_builder, the CLI, the web UI) reads and writes
component + label records through the helpers here.

Behaviour notes:
  • The DB file and its parent dir are created automatically on first use
    (db/components.db by default — see config.DB_PATH).
  • Schema is created with IF NOT EXISTS, so init is idempotent.
  • specs are stored as a JSON string (specs_json) but the read/write helpers
    accept and return native Python objects under the key `specs`.
  • File paths are stored RELATIVE to the project root so the folder can be
    moved without breaking references (see _relativize).
  • upsert uses COALESCE: passing None for a field leaves the existing value
    untouched, so partial updates (e.g. set-image) don't wipe other columns.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import DB_PATH, PROJECT_ROOT


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS components (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    mpn                TEXT    NOT NULL UNIQUE,
    manufacturer       TEXT,
    component_type     TEXT,
    description        TEXT,
    specs_json         TEXT,
    datasheet_url      TEXT,
    datasheet_path     TEXT,
    pinout_image_path  TEXT,
    package_image_path TEXT,
    overrides_json     TEXT,
    status             TEXT    DEFAULT 'ready',
    review_reason      TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mpn            TEXT    NOT NULL,
    label_html     TEXT,
    label_pdf_path TEXT,
    created_at     TEXT    NOT NULL,
    FOREIGN KEY (mpn) REFERENCES components(mpn) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_components_type ON components(component_type);
CREATE INDEX IF NOT EXISTS idx_labels_mpn      ON labels(mpn);
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (used for created_at/updated_at)."""
    return datetime.now(timezone.utc).isoformat()


def _relativize(path: str | Path | None) -> str | None:
    """Normalise a filesystem path for storage: relative to PROJECT_ROOT.

    An absolute path inside the project is rewritten as a relative path; an
    absolute path outside the project (or an already-relative path) is stored
    as-is. None passes straight through. URLs should NOT be passed here.
    """
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(p)  # outside the project tree — keep what we were given
    return str(p)


def _connect() -> sqlite3.Connection:
    """Open a connection, ensuring the DB directory + schema exist first."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")  # enforce the labels→components FK
    conn.executescript(SCHEMA_SQL)  # IF NOT EXISTS throughout — idempotent and cheap
    # Lightweight migration: add columns introduced after a DB was first created
    # (CREATE TABLE IF NOT EXISTS won't alter an existing table). Each ADD COLUMN
    # backfills existing rows with the column default, so old records become
    # status='ready' — they were already approved before the workflow existed.
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(components)")}
    if "overrides_json" not in existing:
        conn.execute("ALTER TABLE components ADD COLUMN overrides_json TEXT")
    if "status" not in existing:
        conn.execute("ALTER TABLE components ADD COLUMN status TEXT DEFAULT 'ready'")
    if "review_reason" not in existing:
        conn.execute("ALTER TABLE components ADD COLUMN review_reason TEXT")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Connection context manager: commits on success, always closes."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_component(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Turn a components row into a dict, decoding specs_json → `specs`."""
    if row is None:
        return None
    d = dict(row)
    raw = d.get("specs_json")
    d["specs"] = json.loads(raw) if raw else None
    ov = d.get("overrides_json")
    d["overrides"] = json.loads(ov) if ov else None
    return d


def _row_to_label(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Turn a labels row into a plain dict."""
    return dict(row) if row is not None else None


# ── Public API: init ──────────────────────────────────────────────────────────

def init_db() -> Path:
    """Create the database file and schema if they don't exist (idempotent).

    Safe to call at startup. Returns the path to the database file.
    """
    with _db():
        pass
    return DB_PATH


# ── Public API: components ────────────────────────────────────────────────────

# The parameter list deliberately mirrors the components table columns — a
# params object would just relocate the same nine names without simplifying.
def upsert_component(  # pylint: disable=too-many-arguments
    mpn: str,
    *,
    manufacturer: str | None = None,
    component_type: str | None = None,
    description: str | None = None,
    specs: Any | None = None,
    datasheet_url: str | None = None,
    datasheet_path: str | Path | None = None,
    pinout_image_path: str | Path | None = None,
    package_image_path: str | Path | None = None,
    overrides: Any | None = None,
    status: str | None = None,
    review_reason: str | None = None,
) -> dict[str, Any]:
    """Insert a new component or update the existing one (keyed on mpn).

    Any field left as None is preserved on update rather than overwritten, so
    this doubles as a partial-update helper (e.g. supply only pinout_image_path
    to override the pinout without disturbing the rest of the record).

    `status`/`review_reason` are a coupled pair and the one exception to the
    "None preserves" rule above: pass `status` to set both at once (the matching
    `review_reason`, which may be None to *clear* a stale reason — e.g. on
    approval); leave `status` as None to preserve both. So you cannot wipe a
    review_reason without also setting a status, which keeps the two consistent.

    `specs` may be any JSON-serialisable object; it is stored as specs_json.
    Path fields are relativised to the project root before storage.

    Returns the full stored record as a dict (with specs decoded).
    """
    now = _utc_now_iso()
    specs_json = json.dumps(specs, ensure_ascii=False) if specs is not None else None
    overrides_json = (
        json.dumps(overrides, ensure_ascii=False) if overrides is not None else None
    )

    with _db() as conn:
        conn.execute(
            """
            INSERT INTO components AS c (
                mpn, manufacturer, component_type, description, specs_json,
                datasheet_url, datasheet_path, pinout_image_path,
                package_image_path, overrides_json, status, review_reason,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'ready'), ?, ?, ?)
            ON CONFLICT(mpn) DO UPDATE SET
                manufacturer       = COALESCE(excluded.manufacturer,       c.manufacturer),
                component_type     = COALESCE(excluded.component_type,     c.component_type),
                description        = COALESCE(excluded.description,        c.description),
                specs_json         = COALESCE(excluded.specs_json,         c.specs_json),
                datasheet_url      = COALESCE(excluded.datasheet_url,      c.datasheet_url),
                datasheet_path     = COALESCE(excluded.datasheet_path,     c.datasheet_path),
                pinout_image_path  = COALESCE(excluded.pinout_image_path,  c.pinout_image_path),
                package_image_path = COALESCE(excluded.package_image_path, c.package_image_path),
                overrides_json     = COALESCE(excluded.overrides_json,     c.overrides_json),
                status             = COALESCE(excluded.status,             c.status),
                -- review_reason is coupled to status: only rewritten (and thus
                -- clearable to NULL) when a new status is supplied, else kept.
                review_reason      = CASE
                                         WHEN ? IS NULL THEN c.review_reason
                                         ELSE excluded.review_reason
                                     END,
                updated_at         = excluded.updated_at
            """,
            (
                mpn,
                manufacturer,
                component_type,
                description,
                specs_json,
                datasheet_url,
                _relativize(datasheet_path),
                _relativize(pinout_image_path),
                _relativize(package_image_path),
                overrides_json,
                status,
                review_reason,
                now,
                now,
                status,  # drives the review_reason CASE in the UPDATE branch
            ),
        )

    stored = get_component(mpn)
    assert stored is not None  # we just wrote it
    return stored


def get_component(mpn: str) -> dict[str, Any] | None:
    """Return the component record for `mpn`, or None if not cached."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM components WHERE mpn = ?", (mpn,)
        ).fetchone()
    return _row_to_component(row)


def component_exists(mpn: str) -> bool:
    """Cheap existence check for the cache-hit branch of the pipeline."""
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM components WHERE mpn = ? LIMIT 1", (mpn,)
        ).fetchone()
    return row is not None


def list_components(component_type: str | None = None) -> list[dict[str, Any]]:
    """List all cached components, optionally filtered by component_type.

    Used by `main.py list` and the web dashboard. Ordered by mpn.
    """
    with _db() as conn:
        if component_type is None:
            rows = conn.execute(
                "SELECT * FROM components ORDER BY mpn"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM components WHERE component_type = ? ORDER BY mpn",
                (component_type,),
            ).fetchall()
    return [c for r in rows if (c := _row_to_component(r)) is not None]


# ── Public API: labels ────────────────────────────────────────────────────────

def save_label(
    mpn: str,
    *,
    label_html: str | None = None,
    label_pdf_path: str | Path | None = None,
) -> dict[str, Any]:
    """Record a generated label for a component. Returns the new label row."""
    now = _utc_now_iso()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO labels (mpn, label_html, label_pdf_path, created_at) "
            "VALUES (?, ?, ?, ?)",
            (mpn, label_html, _relativize(label_pdf_path), now),
        )
        label_id = cur.lastrowid
        assert label_id is not None  # an INSERT always produces a rowid

    label = get_label(label_id)
    assert label is not None
    return label


def get_label(label_id: int) -> dict[str, Any] | None:
    """Fetch a single label row by its primary key."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM labels WHERE id = ?", (label_id,)
        ).fetchone()
    return _row_to_label(row)


def get_labels(mpn: str) -> list[dict[str, Any]]:
    """All labels recorded for a component, newest first."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM labels WHERE mpn = ? ORDER BY created_at DESC, id DESC",
            (mpn,),
        ).fetchall()
    return [lbl for r in rows if (lbl := _row_to_label(r)) is not None]


def get_latest_label(mpn: str) -> dict[str, Any] | None:
    """The most recently created label for a component, or None."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM labels WHERE mpn = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (mpn,),
        ).fetchone()
    return _row_to_label(row)

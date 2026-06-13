"""
core/lookup.py — Nexar (Octopart) API queries + auth.

Refactored from the original `component_lookup` prototype. Owns everything
that talks to the Nexar API:

    get_access_token()    OAuth2 token fetch, with in-memory caching
    search_component()    GraphQL part search (auth handled internally)
    find_datasheet_url()  best datasheet URL from a part record
    print_specs()         console summary of a part record

Credentials and endpoints come from config.py ONLY (which reads them from the
environment / .env). Nothing here is hardcoded.

Token caching: the bearer token and its expiry are held in module-level state,
so repeated lookups (e.g. a batch `add --file parts.txt`) reuse one token and
only re-authenticate when it is about to expire.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass

import requests

import config


# ── Errors ────────────────────────────────────────────────────────────────────

class NexarAuthError(RuntimeError):
    """Raised when authentication is impossible (missing/bad credentials)."""


# ── Token cache ───────────────────────────────────────────────────────────────
# One token per process, refreshed when within _EXPIRY_MARGIN_S of expiring.
# Expiry is tracked with time.monotonic() so wall-clock changes can't break it.

_EXPIRY_MARGIN_S = 60.0  # refresh this many seconds *before* actual expiry


@dataclass
class _TokenCache:
    """Mutable holder for the process-wide cached token."""

    token: str | None = None
    expires_at: float = 0.0  # monotonic deadline; 0 = no token cached


_token_cache = _TokenCache()


def clear_token_cache() -> None:
    """Drop the cached token so the next call re-authenticates (mainly for tests)."""
    _token_cache.token = None
    _token_cache.expires_at = 0.0


def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid Nexar OAuth2 bearer token, fetching one only if needed.

    The token is cached in memory together with its expiry; subsequent calls
    return the cached token until it is within _EXPIRY_MARGIN_S of expiring
    (or `force_refresh=True`).

    Raises NexarAuthError if credentials are not configured or are rejected.
    """
    cached = _token_cache.token
    if (
        not force_refresh
        and cached is not None
        and time.monotonic() < _token_cache.expires_at
    ):
        return cached

    client_id = config.NEXAR_CLIENT_ID
    client_secret = config.NEXAR_CLIENT_SECRET
    if not client_id or not client_secret:
        raise NexarAuthError(
            "Nexar credentials are not configured. Set NEXAR_CLIENT_ID and "
            "NEXAR_CLIENT_SECRET in the environment or a .env file."
        )

    resp = requests.post(
        config.NEXAR_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    if resp.status_code in (400, 401, 403):
        raise NexarAuthError(
            f"Nexar rejected the credentials (HTTP {resp.status_code}). "
            "Check NEXAR_CLIENT_ID / NEXAR_CLIENT_SECRET."
        )
    resp.raise_for_status()

    body = resp.json()
    token: str = body["access_token"]
    # expires_in is seconds-from-now per OAuth2; default conservatively if absent.
    expires_in = float(body.get("expires_in", 600))
    _token_cache.token = token
    _token_cache.expires_at = time.monotonic() + expires_in - _EXPIRY_MARGIN_S

    return token


# ── GraphQL query ─────────────────────────────────────────────────────────────
# Same fields as the prototype; `limit` is now a proper GraphQL variable
# instead of being baked into the query string.

COMPONENT_QUERY = """
query ComponentSearch($query: String!, $limit: Int!) {
  supSearch(q: $query, limit: $limit) {
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        specs {
          attribute { name shortname }
          displayValue
        }
        bestDatasheet { url }
        documentCollections {
          documents { url name }
        }
      }
    }
  }
}
"""


def _post_graphql(payload: dict) -> dict:
    """POST a GraphQL payload to Nexar, retrying once on an expired token.

    A 401 means our cached token went stale early (revoked, clock skew, …);
    force one refresh and retry before giving up.
    """
    for attempt in (1, 2):
        token = get_access_token(force_refresh=attempt == 2)
        resp = requests.post(
            config.NEXAR_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 401 and attempt == 1:
            continue
        resp.raise_for_status()
        return resp.json()
    raise AssertionError("unreachable")  # loop always returns or raises


def search_component(part_number: str, limit: int = 3) -> dict | None:
    """Query Nexar for a part number; return the best-matching part dict.

    Auth is handled internally (cached token, auto-refresh) — callers no
    longer pass a token. Returns None if the search has no results.
    GraphQL-level errors (bad query, quota) raise RuntimeError.
    """
    data = _post_graphql(
        {
            "query": COMPONENT_QUERY,
            "variables": {"query": part_number, "limit": limit},
        }
    )

    if data.get("errors"):
        msgs = "; ".join(e.get("message", "?") for e in data["errors"])
        raise RuntimeError(f"Nexar GraphQL error for '{part_number}': {msgs}")

    results = (data.get("data") or {}).get("supSearch", {}).get("results") or []
    if not results:
        return None
    return results[0]["part"]


def find_datasheet_url(part: dict) -> str | None:
    """Pull the best datasheet URL from a part record.

    Prefers the top-level bestDatasheet field, then falls back to scanning
    documentCollections for the first .pdf link.
    """
    best = part.get("bestDatasheet") or {}
    if best.get("url"):
        return best["url"]

    for coll in part.get("documentCollections") or []:
        for doc in coll.get("documents") or []:
            url = doc.get("url", "")
            if url.lower().endswith(".pdf"):
                return url

    return None


# ── Component-type classification ──────────────────────────────────────────────
# Best-effort guess of the config.COMPONENT_TYPES key for a part, from its
# description, category and spec attribute names. It only has to be good
# enough to pre-fill the label type so the user rarely needs `--type`; a wrong
# guess is harmless (it just picks the template/colour) and is always
# overridable. Returns None when nothing matches, leaving the default template.
#
# Rules are ordered MOST-SPECIFIC-FIRST: keyword-hit counts are tallied per
# type and the highest wins, with ties broken in favour of the earlier (more
# specific) rule. So "ceramic capacitor" beats the generic "capacitor" signal,
# "MOSFET transistor" lands on mosfet not bjt, and a bare "capacitor" falls
# through to ceramic (its generic-term owner) rather than electrolytic.

_CLASSIFY_RULES: list[tuple[str, list[str]]] = [
    ("zener_diode",            ["zener"]),
    ("mosfet",                 ["mosfet", "mos fet", "field effect", "fet",
                                "n channel", "p channel", "rds"]),
    ("bjt_transistor",         ["bjt", "npn", "pnp", "bipolar", "transistor",
                                "collector", "emitter", "hfe"]),
    ("ic_opamp",               ["op amp", "opamp", "operational amplifier",
                                "amplifier", "comparator", "timer", "regulator",
                                "microcontroller", "logic", "integrated circuit",
                                "transceiver", "eeprom", "adc", "dac", "mcu"]),
    ("led",                    ["led", "light emitting"]),
    ("capacitor_electrolytic", ["electrolytic", "tantalum", "aluminum",
                                "aluminium", "polymer"]),
    ("capacitor_ceramic",      ["ceramic", "mlcc", "capacitor", "capacitance",
                                "dielectric", "x7r", "x5r", "c0g", "np0"]),
    ("connector",              ["connector", "header", "receptacle", "socket",
                                "plug", "terminal block", "positions", "pitch",
                                "contacts", "jst", "molex"]),
    ("inductor",               ["inductor", "inductance", "choke",
                                "ferrite bead", "coil"]),
    ("resistor",               ["resistor", "resistance", "ohm", "res"]),
]


def _classify_norm(text: str) -> str:
    """Lowercase, collapse every run of non-alphanumerics to a single space,
    and pad with spaces so keyword phrases match on whole-word boundaries
    (e.g. " led " won't match inside "controlled")."""
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip() + " "


def classify_component_type(part: dict | None) -> str | None:
    """Guess a config.COMPONENT_TYPES key for a Nexar part record.

    Builds a searchable blob from the description, the category (read
    defensively — see note below), and every spec attribute's name/shortname,
    then keyword-matches it against _CLASSIFY_RULES. Returns the best-scoring
    type key, or None if no keyword matched at all.

    Note on `category`: COMPONENT_QUERY does not currently fetch it, but this
    reads `part["category"]` if present (dict with name/ancestors, or a plain
    string) so the guess improves automatically should the query be expanded.
    """
    if not part:
        return None

    blobs: list[str] = []
    if part.get("shortDescription"):
        blobs.append(part["shortDescription"])

    category = part.get("category")
    if isinstance(category, dict):
        blobs.append(category.get("name") or "")
        for ancestor in category.get("ancestors") or []:
            blobs.append((ancestor or {}).get("name") or "")
    elif isinstance(category, str):
        blobs.append(category)

    for spec in part.get("specs") or []:
        attr = spec.get("attribute") or {}
        blobs.append(attr.get("name") or "")
        blobs.append(attr.get("shortname") or "")

    text = _classify_norm(" ".join(blobs))

    best_key: str | None = None
    best_score = 0
    for type_key, keywords in _CLASSIFY_RULES:
        # Match whole words, allowing one trailing 's' so plural category names
        # (e.g. "Connectors", "Resistors") still hit the singular keyword.
        score = sum(1 for kw in keywords if f" {kw} " in text or f" {kw}s " in text)
        if score > best_score:  # strict >: ties keep the earlier, more specific rule
            best_key, best_score = type_key, score
    return best_key


def _rule(width: int = 60) -> str:
    """Horizontal rule for console output; falls back to ASCII if the
    terminal encoding (e.g. Windows cp1252) can't print box-drawing chars."""
    ch = "─"
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        ch.encode(enc)
    except UnicodeEncodeError:
        ch = "-"
    return ch * width


def print_specs(part: dict, max_specs: int = 15) -> None:
    """Print a console summary of a part record (mpn, manufacturer, specs)."""
    print(f"\n{_rule()}")
    print(f"  Part:         {part.get('mpn', 'N/A')}")
    print(f"  Manufacturer: {(part.get('manufacturer') or {}).get('name', 'N/A')}")
    print(f"  Description:  {part.get('shortDescription', 'N/A')}")
    print(_rule())

    specs = part.get("specs") or []
    if specs:
        print("  Specs:")
        for s in specs[:max_specs]:  # cap output to keep it readable
            name = (s.get("attribute") or {}).get("name", "")
            value = s.get("displayValue", "")
            print(f"    {name:<30} {value}")
    print()

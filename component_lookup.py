"""
Component Datasheet Pipeline
-----------------------------
Queries the Octopart API (via Nexar) for component specs and datasheet URLs,
downloads the datasheet PDF, and extracts pages/images likely to contain pinout diagrams.

Requirements:
    pip install requests pymupdf

Setup:
    1. Register at https://nexar.com/api to get a free client_id and client_secret
    2. Set the credentials below or pass via environment variables
"""

import os
import re
import sys
import requests
import fitz  # PyMuPDF
from pathlib import Path


# ── Nexar / Octopart API credentials ─────────────────────────────────────────
# Register free at https://nexar.com/api
CLIENT_ID     = os.getenv("NEXAR_CLIENT_ID",     "NEXAR_CLIENT_ID")
CLIENT_SECRET = os.getenv("NEXAR_CLIENT_SECRET", "NEXAR_CLIENT_SECRET")

NEXAR_TOKEN_URL = "https://identity.nexar.com/connect/token"
NEXAR_API_URL   = "https://api.nexar.com/graphql"

# Keywords used to score pages likely to contain a pinout diagram
PINOUT_KEYWORDS = [
    "pin", "pinout", "pin out", "pin description", "pin configuration",
    "pin assignment", "pin function", "terminal", "package", "connections",
    "diagram", "schematic",
]


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_access_token(client_id: str, client_secret: str) -> str:
    """Fetch a short-lived OAuth2 bearer token from Nexar."""
    resp = requests.post(
        NEXAR_TOKEN_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Octopart / Nexar query ────────────────────────────────────────────────────

COMPONENT_QUERY = """
query ComponentSearch($query: String!) {
  supSearch(q: $query, limit: 3) {
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


def search_component(part_number: str, token: str) -> dict | None:
    """
    Query Nexar for a part number.
    Returns the best-matching part dict, or None if nothing found.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    payload = {
        "query":     COMPONENT_QUERY,
        "variables": {"query": part_number},
    }
    resp = requests.post(NEXAR_API_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()

    data    = resp.json()
    results = data.get("data", {}).get("supSearch", {}).get("results", [])
    if not results:
        return None
    return results[0]["part"]


def find_datasheet_url(part: dict) -> str | None:
    """Pull the best datasheet URL from a part record."""
    # Prefer the top-level bestDatasheet field
    best = part.get("bestDatasheet", {})
    if best and best.get("url"):
        return best["url"]

    # Fall back to documentCollections
    for coll in part.get("documentCollections", []):
        for doc in coll.get("documents", []):
            url = doc.get("url", "")
            if url.lower().endswith(".pdf"):
                return url

    return None


def print_specs(part: dict) -> None:
    """Print a summary of key component specs."""
    print(f"\n{'─'*60}")
    print(f"  Part:         {part.get('mpn', 'N/A')}")
    print(f"  Manufacturer: {part.get('manufacturer', {}).get('name', 'N/A')}")
    print(f"  Description:  {part.get('shortDescription', 'N/A')}")
    print(f"{'─'*60}")

    specs = part.get("specs", [])
    if specs:
        print("  Specs:")
        for s in specs[:15]:   # show first 15 to keep output readable
            name  = s.get("attribute", {}).get("name", "")
            value = s.get("displayValue", "")
            print(f"    {name:<30} {value}")
    print()


# ── PDF download ──────────────────────────────────────────────────────────────

def download_pdf(url: str, dest_path: Path) -> bool:
    """Download a PDF from url to dest_path. Returns True on success."""
    headers = {"User-Agent": "Mozilla/5.0 (component-lookup-script)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30, stream=True)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
        print(f"  Downloaded datasheet → {dest_path}")
        return True
    except Exception as exc:
        print(f"  [!] Could not download PDF: {exc}")
        return False


# ── Pinout page detection & extraction ───────────────────────────────────────

def score_page(page: fitz.Page) -> float:
    """
    Heuristic score for how likely a page contains a pinout/package diagram.
    Higher = more likely to be a pinout page.
    """
    text  = page.get_text().lower()
    score = 0.0

    for kw in PINOUT_KEYWORDS:
        score += text.count(kw) * (2.0 if kw in ("pin", "pinout", "diagram") else 1.0)

    # Pages with lots of images are more likely to contain diagrams
    score += len(page.get_images()) * 1.5

    return score


def extract_pinout_pages(pdf_path: Path, output_dir: Path, top_n: int = 3) -> list[Path]:
    """
    Score every page in the PDF and rasterize the top_n most likely
    pinout/diagram pages as PNG images.

    Returns a list of saved image paths.
    """
    doc    = fitz.open(str(pdf_path))
    scores = [(i, score_page(doc[i])) for i in range(len(doc))]
    scores.sort(key=lambda x: x[1], reverse=True)

    top_pages  = scores[:top_n]
    saved      = []

    output_dir.mkdir(parents=True, exist_ok=True)

    for rank, (page_idx, page_score) in enumerate(top_pages, start=1):
        page = doc[page_idx]
        mat  = fitz.Matrix(2.0, 2.0)   # 2× zoom ≈ 144 DPI — good quality
        pix  = page.get_pixmap(matrix=mat)

        out_path = output_dir / f"pinout_page{rank}_p{page_idx + 1}.png"
        pix.save(str(out_path))
        saved.append(out_path)

        print(f"  Page {page_idx + 1:>3}  score={page_score:6.1f}  → {out_path.name}")

    doc.close()
    return saved


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(part_number: str, output_dir: str = "output") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Authenticate
    print(f"[1/4] Authenticating with Nexar API...")
    try:
        token = get_access_token(CLIENT_ID, CLIENT_SECRET)
    except Exception as exc:
        print(f"  [!] Auth failed: {exc}")
        print("      Make sure CLIENT_ID and CLIENT_SECRET are set correctly.")
        return

    # 2. Search for the component
    print(f"[2/4] Searching for '{part_number}'...")
    part = search_component(part_number, token)
    if not part:
        print(f"  [!] No results found for '{part_number}'")
        return
    print_specs(part)

    # 3. Download the datasheet
    print(f"[3/4] Fetching datasheet...")
    ds_url = find_datasheet_url(part)
    if not ds_url:
        print("  [!] No datasheet URL found for this part.")
        return

    print(f"  URL: {ds_url}")
    safe_name = re.sub(r"[^\w\-]", "_", part.get("mpn", "datasheet")) + ".pdf"
    pdf_path  = out / safe_name

    if not download_pdf(ds_url, pdf_path):
        return

    # 4. Extract likely pinout pages
    print(f"[4/4] Identifying and extracting pinout/diagram pages...")
    pinout_dir = out / "pinout_pages"
    saved = extract_pinout_pages(pdf_path, pinout_dir, top_n=3)

    print(f"\n✓ Done. {len(saved)} pinout page(s) saved to '{pinout_dir}/'")
    print(f"  Full datasheet:  {pdf_path}")


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "NE555"
    run(part)
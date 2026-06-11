"""
core — the engine room of the Component Label System.

Sub-modules (built incrementally, see CLAUDE.md build order):
    cache.py          SQLite read/write layer            ← implemented
    lookup.py         Nexar/Octopart API queries         (todo)
    datasheet.py      PDF download + pinout extraction    (todo)
    label_builder.py  Template population                 (todo)
    pdf_renderer.py   A4 sheet grid → PDF                  (todo)

Nothing is imported here on purpose: importing `core` should be free of
side effects (no DB connection, no config load) until a sub-module is used.
"""

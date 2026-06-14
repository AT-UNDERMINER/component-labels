// Dashboard column sorting — vanilla JS, no frameworks.
//
// Sort state lives entirely in the URL query (?sort=<key>&order=asc|desc) so it
// survives normal navigation (e.g. clicking a type-filter chip) and the browser
// back/forward buttons (handled via popstate). Sorting is applied client-side to
// the rendered table rows, reading clean values from each row's data-* attrs.
(function () {
  const tables = document.querySelectorAll("table.js-sortable");
  if (!tables.length) return;

  // Map a sort key to the row data-attribute that holds its sortable value.
  const KEY_ATTR = { mpn: "data-mpn", type: "data-type", manufacturer: "data-mfr" };
  const ORDER_DIR = { asc: 1, desc: -1 };

  function currentSort() {
    const p = new URLSearchParams(window.location.search);
    const key = p.get("sort");
    const order = p.get("order");
    return {
      key: key && key in KEY_ATTR ? key : null,
      order: order in ORDER_DIR ? order : "asc",
    };
  }

  function sortTable(table, key, order) {
    const tbody = table.querySelector("tbody");
    if (!tbody) return;
    const attr = KEY_ATTR[key];
    const dir = ORDER_DIR[order];
    // Only rows carrying this key participate; a table without the column
    // (e.g. the needs-review table has no Manufacturer) is left untouched.
    const rows = [...tbody.querySelectorAll("tr")].filter((r) => r.hasAttribute(attr));
    if (!rows.length) return;
    rows.sort((a, b) => {
      const av = (a.getAttribute(attr) || "").toLowerCase();
      const bv = (b.getAttribute(attr) || "").toLowerCase();
      // Blanks always sink to the bottom, whichever direction we sort.
      if (av === "" && bv !== "") return 1;
      if (bv === "" && av !== "") return -1;
      return av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" }) * dir;
    });
    rows.forEach((r) => tbody.appendChild(r));
  }

  function paintIndicators(key, order) {
    document.querySelectorAll("table.js-sortable th.sortable").forEach((th) => {
      const active = th.dataset.key === key;
      th.classList.toggle("sorted", active);
      th.setAttribute("aria-sort", active ? (order === "asc" ? "ascending" : "descending") : "none");
      const ind = th.querySelector(".sort-ind");
      if (ind) ind.textContent = active ? (order === "asc" ? " ▲" : " ▼") : "";
    });
  }

  // Keep the type-filter chips carrying the active sort so switching filters
  // (a full page reload) preserves it.
  function syncChips(key, order) {
    document.querySelectorAll(".filters a.chip").forEach((a) => {
      const url = new URL(a.href, window.location.origin);
      url.searchParams.delete("sort");
      url.searchParams.delete("order");
      if (key) {
        url.searchParams.set("sort", key);
        url.searchParams.set("order", order);
      }
      a.href = url.pathname + url.search;
    });
  }

  function apply() {
    const { key, order } = currentSort();
    if (key) tables.forEach((t) => sortTable(t, key, order));
    paintIndicators(key, order);
    syncChips(key, order);
  }

  function activate(th) {
    const key = th.dataset.key;
    if (!key || !(key in KEY_ATTR)) return;
    const cur = currentSort();
    // Same column → reverse; a new column → start ascending.
    const order = cur.key === key && cur.order === "asc" ? "desc" : "asc";
    const url = new URL(window.location.href);
    url.searchParams.set("sort", key);
    url.searchParams.set("order", order);
    history.pushState({ sort: key, order }, "", url.pathname + url.search);
    apply();
  }

  document.querySelectorAll("table.js-sortable th.sortable").forEach((th) => {
    th.addEventListener("click", () => activate(th));
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate(th);
      }
    });
  });

  window.addEventListener("popstate", apply);
  apply();  // apply any sort present in the URL on first load
})();

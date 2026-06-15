// Generic-group print page: live preview + value filter/select. Vanilla JS.
(function () {
  const root = document.querySelector(".generic-print");
  if (!root) return;

  const previewUrl = root.dataset.previewUrl;
  const form = document.getElementById("gp-form");
  const frame = document.getElementById("gp-frame");
  const valuesBox = document.getElementById("gp-values");
  const filter = document.getElementById("gp-filter");
  const selectAll = document.getElementById("gp-all");
  const countEl = document.getElementById("gp-count");

  // Which catalogue value the preview shows (index into the value list).
  let previewIndex = 0;

  // Current parameter picks + preview index → preview query string.
  function previewQuery() {
    const p = new URLSearchParams();
    form.querySelectorAll("input[type=radio]:checked").forEach((r) => p.set(r.name, r.value));
    p.set("i", previewIndex);
    return p.toString();
  }
  function refreshPreview() {
    frame.src = previewUrl + "?" + previewQuery();
  }

  // Parameter radios repaint the preview (e.g. a cap's voltage in the headline).
  form.querySelectorAll('input[type="radio"]').forEach((r) =>
    r.addEventListener("change", refreshPreview));

  // Clicking a value previews it (the checkbox still toggles natively).
  valuesBox.addEventListener("click", (e) => {
    const lab = e.target.closest(".gp-val");
    if (!lab) return;
    previewIndex = lab.dataset.i;
    refreshPreview();
  });

  function updateCount() {
    const n = valuesBox.querySelectorAll('input[type="checkbox"]:checked').length;
    countEl.textContent = n + " selected";
  }
  valuesBox.addEventListener("change", updateCount);

  // "Select all shown" only toggles rows currently visible after filtering.
  selectAll.addEventListener("change", () => {
    valuesBox.querySelectorAll(".gp-val").forEach((lab) => {
      if (lab.style.display !== "none") lab.querySelector("input").checked = selectAll.checked;
    });
    updateCount();
  });

  // Text filter hides non-matching value rows.
  filter.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    valuesBox.querySelectorAll(".gp-val").forEach((lab) => {
      lab.style.display = !q || lab.dataset.text.includes(q) ? "" : "none";
    });
  });

  refreshPreview();
  updateCount();
})();

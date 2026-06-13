// Component Labels — minimal progressive-enhancement JS.

// Print builder: "Select all" toggles every component checkbox.
(function () {
  const master = document.getElementById("select-all");
  if (!master) return;
  const boxes = document.querySelectorAll("input.pick");
  master.addEventListener("change", () => {
    boxes.forEach((b) => { b.checked = master.checked; });
  });
})();

// Visual drag-and-drop label editor. Vanilla JS, HTML5 Drag and Drop API.
(function () {
  const root = document.querySelector(".editor");
  if (!root) return;

  const labelUrl = root.dataset.labelUrl;
  const saveUrl = root.dataset.saveUrl;
  const uploadUrl = root.dataset.uploadUrl;
  const init = JSON.parse(document.getElementById("editor-init").textContent);

  const frame = document.getElementById("preview-frame");
  const elTemplate = document.getElementById("f-template");
  const elHeadline = document.getElementById("f-headline");
  const elValue = document.getElementById("f-value");
  const gallery = document.getElementById("gallery");
  const specList = document.getElementById("spec-list");
  const flash = document.getElementById("preview-flash");

  // Which slot the gallery assigns to, and the current image path per slot.
  let activeSlot = "pinout";
  const images = { pinout: init.pinout || "", package: init.package || "" };

  // ── Current state → preview query ──────────────────────────────────────
  function activeSpecs() {
    return [...specList.querySelectorAll(".spec-row")]
      .filter((li) => li.querySelector("input[type=checkbox]") &&
                      li.querySelector("input[type=checkbox]").checked)
      .map((li) => ({ name: li.dataset.name, value: li.dataset.value }));
  }

  function buildParams() {
    const p = new URLSearchParams();
    p.set("template", elTemplate.value);
    if (elHeadline.value.trim()) p.set("headline", elHeadline.value);
    if (elValue.value.trim()) p.set("subline", elValue.value);
    p.set("specs", JSON.stringify(activeSpecs()));
    p.set("pinout_image", images.pinout || "none");
    p.set("package_image", images.package || "none");
    return p.toString();
  }

  let timer = null;
  function renderPreview() {
    flash.textContent = "updating…";
    fetch(labelUrl + "?" + buildParams())
      .then((r) => r.text())
      .then((html) => { frame.srcdoc = html; flash.textContent = ""; })
      .catch(() => { flash.textContent = "preview error"; });
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(renderPreview, 250); }

  // ── Image gallery + slot toggle ────────────────────────────────────────
  function refreshHighlight() {
    gallery.querySelectorAll(".thumb-btn").forEach((b) => {
      b.classList.toggle("sel", b.dataset.path === images[activeSlot]);
    });
  }
  gallery.addEventListener("click", (e) => {
    const btn = e.target.closest(".thumb-btn");
    if (!btn) return;
    // Click an already-selected thumb to clear that slot.
    images[activeSlot] = (images[activeSlot] === btn.dataset.path) ? "" : btn.dataset.path;
    refreshHighlight();
    renderPreview();
  });
  document.querySelectorAll(".seg-btn").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      activeSlot = b.dataset.slot;
      refreshHighlight();
    }));

  // ── Upload ─────────────────────────────────────────────────────────────
  const upload = document.getElementById("f-upload");
  upload.addEventListener("change", () => {
    if (!upload.files.length) return;
    const fd = new FormData();
    fd.append("file", upload.files[0]);
    fetch(uploadUrl, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d) => {
        if (d.error) { alert(d.error); return; }
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "thumb-btn";
        btn.dataset.path = d.path;
        btn.title = d.name;
        btn.innerHTML = `<img src="${d.url}" alt=""><span>${d.name}</span>`;
        gallery.appendChild(btn);
        images[activeSlot] = d.path;
        refreshHighlight();
        renderPreview();
      })
      .catch(() => alert("Upload failed."))
      .finally(() => { upload.value = ""; });
  });

  // ── Spec toggles (max 3 active) ────────────────────────────────────────
  specList.addEventListener("change", (e) => {
    if (!e.target.matches("input[type=checkbox]")) return;
    if (e.target.checked && specList.querySelectorAll("input:checked").length > 3) {
      e.target.checked = false;
      flashMsg("Max 3 specs");
      return;
    }
    e.target.closest(".spec-row").classList.toggle("active", e.target.checked);
    renderPreview();
  });

  // ── Drag to reorder (HTML5 Drag and Drop) ──────────────────────────────
  let dragEl = null;
  specList.addEventListener("dragstart", (e) => {
    dragEl = e.target.closest(".spec-row");
    if (dragEl) { dragEl.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; }
  });
  specList.addEventListener("dragend", () => {
    if (!dragEl) return;
    dragEl.classList.remove("dragging");
    dragEl = null;
    renderPreview();
  });
  specList.addEventListener("dragover", (e) => {
    if (!dragEl) return;
    e.preventDefault();
    const after = afterElement(e.clientY);
    if (after == null) specList.appendChild(dragEl);
    else specList.insertBefore(dragEl, after);
  });
  function afterElement(y) {
    const rows = [...specList.querySelectorAll(".spec-row:not(.dragging)")];
    let best = { offset: -Infinity, el: null };
    for (const row of rows) {
      const box = row.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > best.offset) best = { offset, el: row };
    }
    return best.el;
  }

  // ── Save / Approve ─────────────────────────────────────────────────────
  // Both buttons POST the same editor state; "approve" adds a flag that also
  // flips the component's status to ready and clears its review reason.
  function submitEditor(approve) {
    const f = new FormData();
    f.append("template", elTemplate.value);
    f.append("headline", elHeadline.value);
    f.append("subline", elValue.value);
    f.append("specs", JSON.stringify(activeSpecs()));
    f.append("pinout_image", images.pinout);
    f.append("package_image", images.package);
    if (approve) f.append("approve", "1");
    const out = document.getElementById("save-flash");
    out.textContent = approve ? "Approving…" : "Saving…";
    fetch(saveUrl, { method: "POST", body: f })
      .then((r) => r.json())
      .then((d) => { if (d.ok) window.location = d.url; else out.textContent = d.error || "Save failed"; })
      .catch(() => { out.textContent = "Save failed"; });
  }

  document.getElementById("save-btn").addEventListener("click", () => submitEditor(false));
  const approveBtn = document.getElementById("approve-btn");
  if (approveBtn) approveBtn.addEventListener("click", () => submitEditor(true));

  function flashMsg(m) {
    flash.textContent = m;
    setTimeout(() => { if (flash.textContent === m) flash.textContent = ""; }, 1500);
  }

  // Wire text/template inputs and do the first render.
  elTemplate.addEventListener("change", renderPreview);
  elHeadline.addEventListener("input", schedule);
  elValue.addEventListener("input", schedule);
  refreshHighlight();
  renderPreview();
})();

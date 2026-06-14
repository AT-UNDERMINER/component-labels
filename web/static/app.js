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

// ── Dashboard: background add (Feature A) + batch import (Feature B) ──────────
(function () {
  const esc = (s) => String(s).replace(/[&<>]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  // Poll a job until it finishes; `render(data)` repaints the status box.
  function pollJob(jobId, render) {
    const tick = () => {
      fetch(`/api/job/${jobId}`)
        .then((r) => r.json())
        .then((data) => {
          render(data);
          if (data.status === "running") setTimeout(tick, 900);
        })
        .catch(() => render({ status: "error", error: "Lost contact with the server." }));
    };
    tick();
  }

  const logHtml = (log) =>
    log && log.length
      ? `<pre class="job-log">${esc(log.join("\n"))}</pre>`
      : "";

  // Feature A — single part lookup.
  const lookupForm = document.getElementById("lookup-form");
  if (lookupForm) {
    const box = document.getElementById("lookup-status");
    lookupForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = lookupForm.querySelector("button");
      btn.disabled = true;
      box.hidden = false;
      box.className = "job-status running";
      box.innerHTML = `<span class="spinner"></span> Starting…`;

      fetch("/api/lookup", { method: "POST", body: new FormData(lookupForm) })
        .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
        .then(({ ok, d }) => {
          if (!ok) throw new Error(d.error || "Request failed.");
          pollJob(d.job_id, (data) => {
            if (data.status === "running") {
              const last = data.log[data.log.length - 1] || "Working…";
              box.className = "job-status running";
              box.innerHTML = `<span class="spinner"></span> ${esc(last)}${logHtml(data.log)}`;
            } else if (data.status === "done") {
              btn.disabled = false;
              box.className = "job-status ok";
              const r = data.result || {};
              box.innerHTML =
                `<strong>✓ Added ${esc(r.mpn || "")}.</strong> ` +
                (r.url ? `<a href="${esc(r.url)}">Open component page →</a>` : "") +
                logHtml(data.log);
            } else {
              btn.disabled = false;
              box.className = "job-status bad";
              box.innerHTML = `<strong>✗ ${esc(data.error || "Failed.")}</strong>` + logHtml(data.log);
            }
          });
        })
        .catch((err) => {
          btn.disabled = false;
          box.className = "job-status bad";
          box.innerHTML = `<strong>✗ ${esc(err.message)}</strong>`;
        });
    });
  }

  // Feature B — batch import.
  const importForm = document.getElementById("import-form");
  if (importForm) {
    const box = document.getElementById("import-status");
    importForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = importForm.querySelector("button[type=submit]");
      btn.disabled = true;
      box.hidden = false;
      box.className = "job-status running";
      box.innerHTML = `<span class="spinner"></span> Uploading…`;

      fetch("/api/import", { method: "POST", body: new FormData(importForm) })
        .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
        .then(({ ok, d }) => {
          if (!ok) throw new Error(d.error || "Upload failed.");
          pollJob(d.job_id, (data) => {
            const r = data.result || {};
            const counts =
              `<span class="tally"><b class="ok">${r.succeeded || 0}</b> ok · ` +
              `<b class="bad">${r.failed || 0}</b> failed · ` +
              `<b>${r.skipped || 0}</b> skipped</span>`;
            if (data.status === "running") {
              box.className = "job-status running";
              box.innerHTML =
                `<span class="spinner"></span> Importing ${r.done || 0}/${r.total || "?"}… ${counts}` +
                logHtml(data.log);
            } else if (data.status === "done") {
              btn.disabled = false;
              box.className = "job-status ok";
              box.innerHTML =
                `<strong>✓ Import complete.</strong> ${counts}` +
                logHtml(data.log) +
                `<p class="small"><a href="/">Refresh dashboard ↻</a></p>`;
            } else {
              btn.disabled = false;
              box.className = "job-status bad";
              box.innerHTML = `<strong>✗ ${esc(data.error || "Failed.")}</strong>` + logHtml(data.log);
            }
          });
        })
        .catch((err) => {
          btn.disabled = false;
          box.className = "job-status bad";
          box.innerHTML = `<strong>✗ ${esc(err.message)}</strong>`;
        });
    });
  }
})();

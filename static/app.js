// Voice input (Chrome, Edge, Safari). Hidden if the browser can't do it.
(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  document.querySelectorAll(".mic").forEach((btn) => {
    btn.hidden = false;
    const box = document.getElementById(btn.dataset.target);
    const hint = btn.closest(".field").querySelector(".mic-hint");
    let rec = null;
    btn.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.continuous = true;
      rec.interimResults = false;
      rec.lang = "en-US";
      rec.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) {
            const t = e.results[i][0].transcript.trim();
            box.value = (box.value ? box.value.replace(/\s*$/, " ") : "") + t;
          }
        }
      };
      rec.onend = () => { rec = null; btn.textContent = "Talk instead"; btn.classList.remove("on"); if (hint) hint.hidden = true; };
      rec.onerror = (e) => { if (e.error === "not-allowed") alert("Allow microphone access in your browser to dictate."); };
      rec.start();
      btn.textContent = "Stop";
      btn.classList.add("on");
      if (hint) hint.hidden = false;
    });
  });
})();

// Show a busy message on slow forms.
document.querySelectorAll("form[data-busy]").forEach((f) => {
  f.addEventListener("submit", () => {
    const b = f.querySelector("button[type=submit]");
    b.disabled = true;
    b.textContent = f.dataset.busy;
  });
});

// Status dropdowns save instantly.
document.querySelectorAll("select.status").forEach((sel) => {
  sel.addEventListener("change", async () => {
    const res = await fetch(`/jobs/${encodeURIComponent(sel.dataset.id)}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: sel.value }),
    });
    if (res.ok) sel.closest("tr").style.opacity = ".45";
    else alert("Couldn't save that. Try reloading the page.");
  });
});

// Search now + live log.
(function () {
  const btn = document.getElementById("run-btn");
  if (!btn) return;
  const log = document.getElementById("log");
  const text = document.getElementById("log-text");
  let timer = null;

  async function poll() {
    const r = await fetch("/run/status");
    if (!r.ok) return;
    const s = await r.json();
    if (s.log) text.textContent = s.log;
    text.scrollTop = text.scrollHeight;
    if (s.running) {
      btn.disabled = true; btn.textContent = "Searching…";
      timer = setTimeout(poll, 4000);
    } else if (timer) {
      timer = null;
      btn.textContent = s.state === "failed" ? "Search hit a problem" : "Done. Reloading…";
      if (s.state !== "failed") setTimeout(() => location.reload(), 1200);
      else btn.disabled = false;
    }
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true; btn.textContent = "Starting…";
    const r = await fetch("/run", { method: "POST" });
    const s = await r.json();
    if (!r.ok) { alert(s.error || "Couldn't start a search."); btn.disabled = false; btn.textContent = "Search now"; return; }
    log.open = true;
    timer = setTimeout(poll, 1500);
  });

  // If a search is already running when the page loads, follow it.
  fetch("/run/status").then((r) => r.json()).then((s) => { if (s.running) { log.open = true; timer = 1; poll(); } });
})();

// Sorting.
(function () {
  const table = document.getElementById("jobs-table");
  if (!table) return;
  const body = table.tBodies[0];
  const select = document.getElementById("sort");
  const textKeys = new Set(["company", "title"]);

  function sortBy(key, dir) {
    const rows = Array.from(body.rows);
    rows.sort((a, b) => {
      let x = a.dataset[key], y = b.dataset[key];
      if (textKeys.has(key)) return dir === "asc" ? x.localeCompare(y) : y.localeCompare(x);
      // Blank values always go last.
      if (x === "" && y === "") return 0;
      if (x === "") return 1;
      if (y === "") return -1;
      return dir === "asc" ? Number(x) - Number(y) : Number(y) - Number(x);
    });
    rows.forEach((r) => body.appendChild(r));
    table.querySelectorAll(".th-sort").forEach((b) => {
      if (b.dataset.key === key) b.dataset.dir = dir; else delete b.dataset.dir;
    });
    const val = key + ":" + dir;
    if ([...select.options].some((o) => o.value === val)) select.value = val;
  }

  select.addEventListener("change", () => { const [k, d] = select.value.split(":"); sortBy(k, d); });
  table.querySelectorAll(".th-sort").forEach((b) => {
    b.addEventListener("click", () => {
      const key = b.dataset.key;
      const dir = b.dataset.dir === "desc" ? "asc" : b.dataset.dir === "asc" ? "desc" : (textKeys.has(key) ? "asc" : "desc");
      sortBy(key, dir);
    });
  });
})();

// Profile: requirements list and remote toggle.
(function () {
  const list = document.getElementById("reqs");
  const tpl = document.getElementById("req-template");
  const add = document.getElementById("req-add");
  if (list && tpl && add) {
    add.addEventListener("click", () => {
      const row = tpl.content.firstElementChild.cloneNode(true);
      list.appendChild(row);
      row.querySelector("input").focus();
    });
    list.addEventListener("click", (e) => {
      if (e.target.classList.contains("req-remove")) e.target.closest(".req-row").remove();
    });
  }
  const remote = document.getElementById("remote_only");
  const loc = document.getElementById("location-field");
  if (remote && loc) remote.addEventListener("change", () => { loc.hidden = remote.checked; });
})();

// Tailor page: wait for writing to finish.
(function () {
  const box = document.getElementById("writing");
  if (!box) return;
  async function poll() {
    try {
      const r = await fetch(box.dataset.stateUrl);
      const s = await r.json();
      if (s.state !== "writing") { location.reload(); return; }
    } catch (e) { /* try again */ }
    setTimeout(poll, 4000);
  }
  setTimeout(poll, 4000);
})();

// Tailor page: mark as applied.
(function () {
  const btn = document.getElementById("mark-applied");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const r = await fetch(btn.dataset.url, { method: "POST" });
    if (r.ok) { btn.textContent = "Marked as applied"; btn.disabled = true; }
    else alert("Couldn't mark it. Try reloading the page.");
  });
})();

// Tailor page: editor, drafts, downloads.
(function () {
  const panel = document.getElementById("doc-panel");
  if (!panel) return;
  const resume = document.getElementById("resume-editor");
  const cover = document.getElementById("cover-editor");
  const editors = [resume, cover].filter(Boolean);
  const state = document.getElementById("save-state");
  const saveBtn = document.getElementById("save-draft");
  let dirty = false, savedRange = null;

  try { document.execCommand("styleWithCSS", false, false); } catch (e) {}

  const inEditor = (node) => editors.some((ed) => ed.contains(node));
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (sel.rangeCount && inEditor(sel.anchorNode)) savedRange = sel.getRangeAt(0).cloneRange();
  });
  function restore() {
    if (!savedRange) return false;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(savedRange);
    return true;
  }

  editors.forEach((ed) => ed.addEventListener("input", () => {
    dirty = true;
    state.textContent = "Unsaved changes";
  }));

  const toolbar = document.getElementById("toolbar");
  toolbar.querySelectorAll("button").forEach((b) => {
    b.addEventListener("mousedown", (e) => e.preventDefault());   // keep the selection
    b.addEventListener("click", () => {
      restore();
      document.execCommand(b.dataset.cmd, false, null);
      dirty = true; state.textContent = "Unsaved changes";
    });
  });
  toolbar.querySelectorAll("select").forEach((s) => {
    s.addEventListener("change", () => {
      if (!s.value) return;
      if (!restore()) { alert("Select some text in a document first."); s.value = ""; return; }
      document.execCommand(s.dataset.cmd, false, s.value);
      s.value = "";
      dirty = true; state.textContent = "Unsaved changes";
    });
  });

  async function save() {
    state.textContent = "Saving…";
    const r = await fetch(panel.dataset.saveUrl, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_html: resume ? resume.innerHTML : "", cover_html: cover ? cover.innerHTML : "" }),
    });
    if (!r.ok) { state.textContent = "Couldn't save. Try again."; return false; }
    dirty = false;
    state.textContent = "Draft saved";
    return true;
  }
  saveBtn.addEventListener("click", save);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
  });

  // Downloads always include your latest edits.
  panel.querySelectorAll("a.dl").forEach((a) => {
    a.addEventListener("click", async (e) => {
      if (!dirty) return;
      e.preventDefault();
      if (await save()) window.location.href = a.href;
    });
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });
})();

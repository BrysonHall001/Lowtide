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

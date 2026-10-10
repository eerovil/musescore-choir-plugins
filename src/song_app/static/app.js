"use strict";

const app = document.getElementById("app");
const crumb = document.getElementById("crumb");

// ---- tiny helpers --------------------------------------------------------
const el = (tag, props = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.includes("-")) e.setAttribute(k, v); // data-*/aria-* must be real attributes
    else e[k] = v;
  }
  for (const k of kids.flat()) e.append(k?.nodeType ? k : document.createTextNode(k ?? ""));
  return e;
};
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}
const getJSON = (p) => api(p);
const postJSON = (p, body) =>
  api(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });

const DEFAULT_TOP_MARGIN = 0;
const DEFAULT_BOTTOM_MARGIN = 5;
const STAGE_LABEL = { register: "Start", scan: "Scan", clean: "Clean", fix: "Fix", lyrics: "Lyrics", review: "Review", record: "Record", upload: "Upload" };

const stageMenu = document.getElementById("stagemenu");

// The phone's stage drawer (#258). Opening it adds a history entry, so Android's Back
// closes the drawer rather than leaving the song; every other way of closing it goes
// back through that entry too, so the history never collects stale ones.
const drawerWs = () => document.querySelector(".ws.drawer-open");
const markDrawer = (ws, open) => {
  ws.classList.toggle("drawer-open", open);
  stageMenu.setAttribute("aria-expanded", open ? "true" : "false");
};
function openDrawer(ws) {
  if (ws.classList.contains("drawer-open")) return;
  markDrawer(ws, true);
  history.pushState({ drawer: true }, "");
}
function closeDrawer() {
  const ws = drawerWs();
  if (!ws) return;
  markDrawer(ws, false);
  if (history.state && history.state.drawer) history.back();
}
window.addEventListener("popstate", () => { const ws = drawerWs(); if (ws) markDrawer(ws, false); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// On a phone the bottom bar is the way around a song, so the page itself must never
// end up scrolled: it does not scroll on its own (html and body are overflow:hidden),
// but a browser scrolling a focused field into view still can, and leaves it there
// after the keyboard closes — the bar is then below the screen (#258). Put it back
// whenever the page scrolls, the window's shape changes or a field lets go of the
// focus.
const unscrollPage = () => {
  const root = document.scrollingElement || document.documentElement;
  if (root.scrollTop || root.scrollLeft) root.scrollTo(0, 0);
};
window.visualViewport?.addEventListener("resize", unscrollPage);
window.addEventListener("scroll", unscrollPage, { passive: true });
window.addEventListener("resize", unscrollPage);
window.addEventListener("orientationchange", unscrollPage);
document.addEventListener("focusout", () => setTimeout(unscrollPage, 0));

// ---- router --------------------------------------------------------------
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);

function route() {
  const m = location.hash.match(/^#\/song\/(.+)$/);
  if (m) renderWorkspace(decodeURIComponent(m[1]));
  else renderLibrary();
}

// ---- library -------------------------------------------------------------
const SORTS = {
  updated: { label: "Last edited", fn: (a, b) => (b.updated_at || 0) - (a.updated_at || 0) },
  created: { label: "Created", fn: (a, b) => (b.created_at || 0) - (a.created_at || 0) },
  name: { label: "Name", fn: (a, b) => a.name.localeCompare(b.name) },
};

async function renderLibrary() {
  crumb.textContent = "";
  stageMenu.hidden = true;     // the Library has no stages
  const songs = await getJSON("/api/songs");
  const sortKey = SORTS[localStorage.getItem("songSort")] ? localStorage.getItem("songSort") : "updated";
  songs.sort(SORTS[sortKey].fn);
  const cards = songs.map((s) => {
    const stageDone = (st, i) =>
      i < s.stage_index || (st === "record" && s.recorded) || (st === "upload" && s.uploaded);
    const rail = el("div", { className: "rail" },
      s.stages.map((st, i) =>
        el("span", { className: "dot " + (stageDone(st, i) ? "done" : i === s.stage_index ? "now" : "") })));
    const badge = s.uploaded
      ? el("span", { className: "badge good" }, "✓ Recorded & uploaded")
      : s.recorded
      ? el("span", { className: "badge good" }, "✓ Recorded")
      : s.open_issues
      ? el("span", { className: "badge warn" }, `${s.open_issues} issue(s) to fix`)
      : el("span", { className: "badge" }, STAGE_LABEL[s.stage] || s.stage);
    return el("a", { className: "card", href: `#/song/${encodeURIComponent(s.slug)}` },
      el("h3", {}, s.name), rail, badge);
  });
  const sortSel = el("select", {
    onchange: () => { localStorage.setItem("songSort", sortSel.value); renderLibrary(); },
  }, Object.entries(SORTS).map(([k, v]) =>
    el("option", { value: k, selected: k === sortKey }, "Sort: " + v.label)));

  app.replaceChildren(
    el("div", { className: "lib" },
      el("div", { className: "lib-head" },
        el("h1", {}, "Songs"),
        el("div", { className: "row" },
          sortSel,
          el("button", { onclick: async (e) => {
            const b = e.target; b.disabled = true; b.textContent = "Importing…";
            try { const { imported } = await postJSON("/api/import"); renderLibrary();
                  if (!imported) alert("No new folders to import."); }
            catch (err) { b.disabled = false; b.textContent = "Import existing"; alert(err.message); }
          } }, "Import existing"),
          el("button", { className: "primary", onclick: newSongDialog }, "+ New song"))),
      cards.length ? el("div", { className: "cards" }, cards)
                   : el("p", { className: "hint" }, "No songs yet. Create one to begin.")));
}

// ---- installing homr ---------------------------------------------------------
// Which homr this host reads scans with, whether the fork's main has moved past
// it, and a button that runs scripts/install-homr.sh (#249). Updating is a press,
// never automatic: the day a parse changes is a day somebody chose. It lives in
// the Scan panel (#261), where homr is used and where "not installed" is said.
const short = (c) => (c ? c.slice(0, 7) : "");

function homrBox() {
  const line = el("p", { className: "homr-line" }, "homr: checking…");
  const btn = el("button", { hidden: true });
  const again = el("button", { hidden: true }, "Reinstall");
  const out = el("pre", { className: "log homr-log", hidden: true });
  const box = el("details", { className: "homr-box" },
    el("summary", {}, "homr — reads scanned pages"), line,
    el("div", { className: "row" }, btn, again), out);
  let timer = null;

  function draw(st) {
    const installed = st.installed ? `${st.label || "installed"}` : null;
    const r = st.result || {};
    if (st.running) {
      line.textContent = "Installing homr… this takes a few minutes the first time.";
    } else if (r.finished_at && !r.ok) {
      line.textContent = "Install failed — see the log below.";
    } else if (!installed) {
      line.textContent = "homr: not installed. Scanning a PDF needs it.";
    } else if (st.up_to_date) {
      line.textContent = `homr: ${installed} — up to date`;
    } else if (st.up_to_date === false) {
      line.textContent = `homr: ${installed} — newer on GitHub: main @ ${short(st.latest)}`;
    } else {
      line.textContent = `homr: ${installed} — couldn't reach GitHub to check for a newer one`;
    }
    if (r.finished_at && r.ok && !st.running) {
      line.textContent = `Installed ${installed || "homr"}` + (st.up_to_date ? " — up to date" : "");
    }
    btn.disabled = !!st.running;
    btn.textContent = st.running ? "Installing…" : installed ? "Update homr" : "Install homr";
    btn.hidden = !st.running && !!installed && st.up_to_date !== false;
    again.hidden = st.running || !installed || !btn.hidden;
    const lines = st.log || [];
    out.hidden = !lines.length;
    out.textContent = lines.join("\n");
    out.scrollTop = out.scrollHeight;
    if (st.running || r.finished_at && !r.ok || !installed) box.open = true;
  }

  async function poll() {
    clearTimeout(timer);
    if (!box.isConnected) return;  // the panel was left or redrawn; stop asking
    try {
      const st = await getJSON("/api/homr/install");
      draw(st);
      if (st.running) timer = setTimeout(poll, 2000);
    } catch (err) { line.textContent = "homr: " + err.message; }
  }

  async function install() {
    btn.disabled = again.disabled = true;
    try { await postJSON("/api/homr/install"); }
    catch (err) { alert(err.message); }
    again.disabled = false;
    poll();
  }
  btn.onclick = install;
  again.onclick = () => {
    if (confirm("Install homr again from the fork's main? It is already up to date.")) install();
  };
  getJSON("/api/homr/install").then((st) => {
    draw(st);
    if (st.running) timer = setTimeout(poll, 2000);
  }).catch((err) => { line.textContent = "homr: " + err.message; });
  return box;
}

function newSongDialog() {
  const name = el("input", { placeholder: "Song name, e.g. Laulun aika" });
  // Which voices sing it. Asked here because nothing in the file can settle it:
  // a male-choir score is written in treble sounding an octave down and editions
  // routinely leave the 8 off the clef, so its tenor line reads as a soprano one.
  const voicing = el("select", {},
    el("option", { value: "" }, "Choose…"),
    el("option", { value: "men" }, "Men (TTBB)"),
    el("option", { value: "women" }, "Women (SSAA)"),
    el("option", { value: "mixed" }, "Mixed (SATB)"));
  // The PDF leads and the score file is the fallback, because that is now the
  // ordinary way in: a PDF on its own is a song and the app reads the score off
  // the page (#127). Handing in a score instead is the manual import (#86).
  // Ids because the two are told apart by name in the browser tests, not by order.
  const pdf = el("input", { type: "file", accept: ".pdf", id: "f-pdf" });
  const xml = el("input", { type: "file", accept: ".mscx,.mscz,.musicxml,.xml", id: "f-xml" });
  const status = el("p", { className: "hint newstatus" });
  // The two doors go to different stages, so the form says which one is open
  // before Create is pressed rather than leaving it to be read off the stage rail.
  const routeHint = el("p", { className: "hint routehint" });
  const sayRoute = () => {
    routeHint.textContent = xml.files[0]
      ? (pdf.files[0]
         ? "Starts at Clean — the score you handed in is used, so the page is not scanned. The PDF is kept for the lyrics."
         : "Starts at Clean — scanning is skipped, because the score is already here.")
      : pdf.files[0]
      ? "Starts at Scan — mark the printed systems on the page, then read the score off them."
      : "A PDF alone starts at Scan; a score file starts at Clean and skips scanning.";
  };
  // A blank name falls back to the file's own name, the PDF first (#241). It is
  // filled into the box so it can be read and edited; a typed name is never
  // replaced. The server applies the same rule to a blank name.
  const fileName = () => {
    const f = pdf.files[0] || xml.files[0];
    return f ? f.name.replace(/\.[^.]*$/, "").replace(/_/g, " ").replace(/\s+/g, " ").trim() : "";
  };
  let autoName = "";
  const sayName = () => {
    if (name.value.trim() && name.value !== autoName) return;
    autoName = fileName();
    name.value = autoName;
  };
  pdf.onchange = () => { sayRoute(); sayName(); };
  xml.onchange = () => { sayRoute(); sayName(); };
  sayRoute();
  const create = el("button", { className: "primary", onclick: async () => {
    if (!pdf.files[0] && !xml.files[0]) {
      status.textContent = "A score PDF or a score file is required — give at least one.";
      return;
    }
    if (!voicing.value) { status.textContent = "Choose who sings it — it decides the part names."; return; }
    const fd = new FormData();
    fd.append("name", name.value.trim() || fileName());
    fd.append("voicing", voicing.value);
    if (xml.files[0]) fd.append("xml", xml.files[0]);
    if (pdf.files[0]) fd.append("pdf", pdf.files[0]);
    status.textContent = "Creating…";
    try {
      const { slug } = await api("/api/songs", { method: "POST", body: fd });
      location.hash = `#/song/${encodeURIComponent(slug)}`;
    } catch (e) { status.textContent = e.message; }
  }}, "Create");

  app.replaceChildren(el("div", { className: "lib" },
    el("h1", {}, "New song"),
    el("label", {}, "Name"), name,
    el("label", {}, "Score PDF (.pdf)"), pdf,
    el("p", { className: "hint" }, "The printed page. Scanned and read into a score, and used for the lyrics."),
    el("label", {}, "Score file (.mscx / .mscz / .musicxml / .xml)"), xml,
    el("p", { className: "hint" }, "Optional — a score you already have. Hand one in and the page is not scanned."),
    routeHint,
    el("label", {}, "Who sings it"), voicing,
    el("div", { className: "row" }, create, el("button", { onclick: renderLibrary }, "Cancel")),
    status));
}

// ---- workspace -----------------------------------------------------------
let ws = null;
let logBox = null;
let lyricScroll = null; // carry the lyrics panel scroll across a re-import re-render

async function renderWorkspace(slug) {
  if (ws) { ws.close(); ws = null; }
  const song = await getJSON(`/api/songs/${encodeURIComponent(slug)}`);
  crumb.textContent = "› " + song.name;
  let view = song.stage; // which panel is shown
  // Scanning opens on the Systems editor, not on the PDF: drawing the bands is
  // the thing that must happen before anything else can, so it is the thing in
  // front of you (#103).
  const firstDoc = song.stage === "review" && song.lyrics
    ? "cleaned" : song.stage === "record" ? "preview"
    : song.stage === "scan" && song.has_pdf ? "systems"
    : song.has_pdf ? "pdf" : "original";
  const panes = [firstDoc]; // 1 or 2 docs shown side by side
  let viewFp = song.cleaned_fingerprint; // viewer is only rebuilt when this changes
  resetSysVersions(slug, song.cleaned_fingerprint);
  let recordPreviewSettings = null;

  // Build the shell once. The viewer (and its rendered previews) is NOT recreated on
  // stage or panel changes, so the previews keep their loaded state and scroll position.
  const stagebarEl = el("div", { className: "stagebar" });
  const panelEl = el("div", { className: "panel" });
  let viewerEl = viewer(song, slug, panes, rebuildViewer, view,
    () => recordPreviewSettings);
  const wsGrid = el("div", { className: "ws" }, stagebarEl, panelEl, viewerEl);
  // Reviewing means reading two scores side by side; the rail and panel are just
  // in the way then. Remembered, because it is a mode you stay in.
  // classList rather than className: the phone layout keeps its own class here too.
  const setWide = (on) => {
    wsGrid.classList.toggle("wide", on);
    localStorage.setItem("wsWide", on ? "1" : "0");
    wideBtn.textContent = on ? "❯ Show panel" : "❮ Hide panel";
    wideBtn.title = on ? "show the stage panel" : "hide the stage panel for more room";
  };
  const wideBtn = el("button", { className: "widetoggle",
    onclick: () => setWide(!wsGrid.classList.contains("wide")) });
  wsGrid.append(wideBtn);

  // On a phone the panel and the viewer cannot share the screen, so one is shown at a
  // time and this bar switches between them. The stage list is the same left-hand
  // sidebar as on desktop, slid in over the page by the header's ☰ (#258). The bar and
  // the backdrop are in the DOM at every width — the stylesheet hides them above the
  // breakpoint, so the desktop layout is untouched and there is no width-sniffing in
  // here to disagree with the media query.
  const paneBtns = {};
  let pane = "panel";
  const showPane = (p) => {
    if (p !== "viewer") viewerEl._pausePreview();
    pane = p;
    for (const k of ["panel", "viewer"]) wsGrid.classList.toggle("m-" + k, k === p);
    for (const k in paneBtns) paneBtns[k].className = "mtab" + (k === p ? " active" : "");
    // A pane that was hidden has no width, so its PDF could not render while it was
    // away; now that it is on screen, let it.
    if (p === "viewer") viewerEl._wake();
  };
  const mobilebar = el("div", { className: "mobilebar" },
    [["panel", "Panel"], ["viewer", "Score"]].map(([k, label]) =>
      (paneBtns[k] = el("button", { className: "mtab", onclick: () => showPane(k) }, label))));
  const backdrop = el("div", { className: "drawer-backdrop", onclick: closeDrawer });
  wsGrid.append(mobilebar, backdrop);
  // Assigned, not added: each song's render replaces the last one's handler.
  stageMenu.onclick = () =>
    wsGrid.classList.contains("drawer-open") ? closeDrawer() : openDrawer(wsGrid);
  stageMenu.hidden = false;
  stageMenu.setAttribute("aria-expanded", "false");

  app.replaceChildren(wsGrid);
  setWide(localStorage.getItem("wsWide") === "1");
  showPane("panel");

  function drawStagebar() {
    const rec = song.record || {};
    const recorded = !!(rec.outputs && rec.outputs.length);
    const uploaded = !!song.upload_status?.complete;
    const done = (st, i) =>
      i < song.stage_index || (st === "record" && recorded) || (st === "upload" && uploaded);
    stagebarEl.replaceChildren(...song.stages.map((st, i) => el("div", {
      className: "step " + (st === view ? "active " : "") + (done(st, i) ? "done" : ""),
      // Picking a stage on a phone means "take me to it", so the panel comes forward.
      onclick: () => selectStage(st),
    }, STAGE_LABEL[st] || st)));
    // The middle button names the stage it will show, so the bar says where you are.
    paneBtns.panel.textContent = STAGE_LABEL[view] || view;
    paneBtns.viewer.textContent = view === "record" ? "Preview" : "Score";
  }

  function drawPanel() {
    recordPreviewSettings = null;
    panelEl._keepPlace = null;
    panelEl._refreshInPlace = null;
    panelEl.replaceChildren();
    renderPanel(panelEl, view, song, slug, refresh, {
      // Use the same click path as the rail so rendering_state.js remembers an
      // action-driven Review → Record transition across reloads as well.
      selectStage: (stage) => {
        const target = [...stagebarEl.querySelectorAll(".step")]
          .find((step) => step.textContent.trim() === (STAGE_LABEL[stage] || stage));
        if (target) target.click();
        else selectStage(stage);
      },
      openPreview: ({ start } = {}) => {
        viewerEl._showFirst("preview");
        showPane("viewer");
        if (start) viewerEl._startPreview();
      },
      // The panel is a task screen and the score is a full-screen visual, so a
      // panel that says "look at this" has to be able to put it in front of you.
      openDoc: (doc) => { viewerEl._showFirst(doc); showPane("viewer"); },
      pausePreview: () => viewerEl._pausePreview(),
      previewInputsChanged: () => viewerEl._syncPreview(),
      setPreviewSettings: (settings) => { recordPreviewSettings = settings; },
    });
  }

  function selectStage(stage) {
    closeDrawer();
    view = stage;
    if (view === "review" && song.lyrics) panes[0] = "cleaned";
    else if (view === "record") panes[0] = "preview";
    else if (view === "scan" && song.has_pdf) panes[0] = "systems";
    drawStagebar();
    drawPanel();
    if (tabKeys() !== builtTabs) rebuildViewer();
    else viewerEl._showFirst(panes[0]);
    showPane("panel");
  }

  function rebuildViewer() {
    const next = viewer(song, slug, panes, rebuildViewer, view,
      () => recordPreviewSettings);
    viewerEl._destroyPreview();
    wsGrid.replaceChild(next, viewerEl);
    viewerEl = next;
    builtTabs = tabKeys();
  }

  const tabKeys = () => viewerTabs(song, view).map(([k]) => k).join(",");
  // What the viewer was actually built with. Comparing against this rather than
  // against a before/after of `song` matters: a caller that already merged the
  // server's reply into `song` before calling refresh() would otherwise see no
  // change and the new tab would appear only on reload.
  let builtTabs = tabKeys();

  async function refresh() {
    const fresh = await getJSON(`/api/songs/${encodeURIComponent(slug)}`);
    Object.assign(song, fresh);
    drawStagebar();
    // A refresh redraws the panel the person is looking at, so a panel that loads
    // its content later says where it was first (the Fix panel, #329).
    panelEl._keepPlace?.();
    // A panel that can bring itself up to date says so, and is left standing: the
    // Lyrics panel, so an import does not throw away the box being typed in and
    // the place in the list (#336). It answers false when what it shows is stale.
    if (!panelEl._refreshInPlace?.()) drawPanel();
    if (song.cleaned_fingerprint !== viewFp) cleanedScoreMoved(slug, song.cleaned_fingerprint);
    if (tabKeys() !== builtTabs) {
      // The tab set changed (a doc appeared or vanished) → structural rebuild.
      viewFp = song.cleaned_fingerprint;
      rebuildViewer();
    } else if (song.cleaned_fingerprint !== viewFp) {
      // Score changed: reload only the cleaned renders, keep PDF/original scroll.
      viewFp = song.cleaned_fingerprint;
      viewerEl._refreshFp(viewFp);
    } else {
      viewerEl._refreshScan();
    }
    viewerEl._syncPreview();
  }

  drawStagebar();
  drawPanel();

  // live updates: logs + state pings
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(slug)}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "log") appendLog(msg.line);
    else if (msg.type === "error") appendLog(msg.line, true);
    else if (msg.type === "progress") updateProgress(msg.line);
    else if (msg.type === "state") refresh();
  };
}

function viewerTabs(song, view) {
  const tabs = [];
  if (song.has_pdf) tabs.push(["pdf", "Original PDF"]);
  if (song.has_pdf) tabs.push(["systems", "Systems"]);
  // The scan against the page it was read off, system by system. Offered as soon
  // as one band has been read — checking the first few while the rest are still
  // being read is exactly when a bad reading is cheapest to catch.
  if (song.has_pdf && song.scan_status?.read) tabs.push(["scanned", "Scan vs page"]);
  if (song.systems) tabs.push(["system", "One system"]);
  if (song.systems && song.has_cleaned) tabs.push(["compare", "Compare"]);
  tabs.push(["original", "Original XML"]);
  if (song.has_cleaned) {
    tabs.push(["cleaned_nolyrics", "Cleaned MSCX"]);
    // Only once there are lyrics to see. Offering it beforehand renders a score
    // identical to the tab next to it, with nothing to say why.
    if (song.lyrics) tabs.push(["cleaned", "Cleaned MSCX with lyrics"]);
    if (view === "record") tabs.push(["preview", "Preview"]);
  }
  return tabs;
}

function docUrl(slug, doc, fp) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  return doc === "pdf" ? `${P}/pdf` : `${P}/render?doc=${doc}&v=${encodeURIComponent(fp || "")}`;
}

// ---- pdf.js: render PDFs into our own scroll container so scroll survives reloads --
const PDFJS_BASE = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174";
let pdfjsReady = null;
function ensurePdfjs() {
  if (pdfjsReady) return pdfjsReady;
  pdfjsReady = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = `${PDFJS_BASE}/pdf.min.js`;
    s.onload = () => {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS_BASE}/pdf.worker.min.js`;
      resolve(window.pdfjsLib);
    };
    s.onerror = () => reject(new Error("pdf.js failed to load"));
    document.head.append(s);
  });
  return pdfjsReady;
}

// The viewer's own zoom (#258). On a phone the page itself cannot be pinch-zoomed —
// zooming the page is what carried the bottom bar off the screen — so the score is
// zoomed here instead, by drawing it again at the new size rather than stretching
// a picture. `MAX_PDF_PIXELS` caps a page's drawn width: at zoom 4 on a 3x screen a
// page would otherwise be a canvas of ~28M pixels, and a score has several.
const MIN_ZOOM = 1, MAX_ZOOM = 4, MAX_PDF_PIXELS = 2400;
const clampZoom = (z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

// Render `url` into `view` (a scrollable div) at `view._zoom`, preserving its current
// scroll. `anchor` names a point on screen ({x, y} inside the view) that should stay
// over the same spot of the music, which is what a zoom wants; without one the
// scroll position is kept as it is (same zoom → same content height → exact).
async function renderPdf(view, url, anchor) {
  const token = (view._tok = (view._tok || 0) + 1);
  const lib = await ensurePdfjs();
  const pdf = await lib.getDocument(url).promise;
  if (view._tok !== token) return; // superseded by a newer render
  const width = view.clientWidth || 600;
  const zoom = view._zoom || 1;
  const cssWidth = (width - 14) * zoom;
  const ratio = Math.min(window.devicePixelRatio || 1, MAX_PDF_PIXELS / cssWidth);
  const canvases = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    if (view._tok !== token) return;
    const base = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: (cssWidth / base.width) * Math.max(ratio, 1 / zoom) });
    const c = el("canvas", { className: "pdfpage" });
    c.width = vp.width; c.height = vp.height;
    c.style.width = `${cssWidth}px`;
    if (zoom > 1) c.style.maxWidth = "none";
    await page.render({ canvasContext: c.getContext("2d"), viewport: vp }).promise;
    if (view._tok !== token) return;
    canvases.push(c);
  }
  let fx, fy;
  if (anchor) {
    fx = (view.scrollLeft + anchor.x) / Math.max(1, view.scrollWidth);
    fy = (view.scrollTop + anchor.y) / Math.max(1, view.scrollHeight);
  }
  const top = view.scrollTop, left = view.scrollLeft;
  view.style.transform = "";           // a pinch in progress is now drawn for real
  view.replaceChildren(...canvases);
  if (anchor) {
    view.scrollLeft = fx * view.scrollWidth - anchor.x;
    view.scrollTop = fy * view.scrollHeight - anchor.y;
  } else {
    view.scrollTop = top;
    view.scrollLeft = left;
  }
}

// Draw `view` again at `zoom`, keeping `anchor` (a point inside the view; its centre
// by default) over the same spot of the music.
function zoomPdf(view, zoom, anchor) {
  zoom = clampZoom(zoom);
  if (!view._url || view._renderedUrl !== view._url) { view._zoom = zoom; return; }
  if (zoom === (view._zoom || 1)) { view.style.transform = ""; return; }
  view._zoom = zoom;
  renderPdf(view, view._url,
    anchor || { x: view.clientWidth / 2, y: view.clientHeight / 2 }).catch(() => {});
}

// Two fingers on the score zoom the score, not the page. While the fingers move the
// view is only scaled, which is cheap; on release it is drawn again at that size.
// Touch events rather than pointer events: with page panning still allowed, the
// browser cancels a pointer as soon as it starts scrolling, and a pinch would die
// half-way through.
function pinchZoom(view) {
  let start = null;
  const spread = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  view.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 2 || !view._url) return;
    e.preventDefault();
    const r = view.getBoundingClientRect();
    const t = e.touches;
    start = {
      d: spread(t), scale: 1,
      x: (t[0].clientX + t[1].clientX) / 2 - r.left,
      y: (t[0].clientY + t[1].clientY) / 2 - r.top,
    };
    view.style.transformOrigin = `${start.x + view.scrollLeft}px ${start.y + view.scrollTop}px`;
  }, { passive: false });
  view.addEventListener("touchmove", (e) => {
    if (!start || e.touches.length !== 2) return;
    e.preventDefault();
    const zoom = view._zoom || 1;
    start.scale = clampZoom(zoom * spread(e.touches) / start.d) / zoom;
    view.style.transform = `scale(${start.scale})`;
  }, { passive: false });
  const end = () => {
    if (!start) return;
    const { scale, x, y } = start;
    start = null;
    zoomPdf(view, (view._zoom || 1) * scale, { x, y });
  };
  view.addEventListener("touchend", end);
  view.addEventListener("touchcancel", end);
}

// "Still working", with the seconds counting up. A MuseScore render can take a
// minute, and a line that never changes reads as a page that has frozen (#303).
// The counter stops by itself once the note has left the page.
function busyNote(text, className = "busynote") {
  const note = el("p", { className }, text);
  const started = Date.now();
  const timer = setInterval(() => {
    if (!note.isConnected) { clearInterval(timer); return; }
    note.textContent = `${text} ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  return note;
}

// Fetch `url` once; a refusal throws with the server's own words. These URLs are
// MuseScore runs, so the reason is read off the response that failed rather than
// by asking again, which would run the same failing render a second time.
async function fetchOrSay(url) {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error((await r.json().catch(() => ({}))).detail || r.statusText || "");
  }
  return r;
}

// Render with pdf.js; fall back to a native iframe only if pdf.js itself can't
// load (offline). While it renders the pane says so: a first build shows a note
// in place of the score, a rebuild keeps the old score up under an "Updating…"
// badge, since blanking a score somebody is reading is worse than a few seconds
// of the previous one. A render the server refused says why, rather than leaving
// the pane empty, which looked the same as waiting forever.
async function mountPdf(view, url) {
  const mine = (view._mountTok = (view._mountTok || 0) + 1);
  const latest = () => view._mountTok === mine;
  const busy = view._busyText || "Loading…";
  const shown = view.querySelector("canvas.pdfpage");
  let badge = null;
  if (shown) {
    badge = view.querySelector(".vupdating")
      || el("div", { className: "vupdating" }, "Updating…");
    if (!badge.isConnected) view.prepend(badge);
  } else if (!view.querySelector(".busynote")) {
    view.replaceChildren(busyNote(busy));
  }
  try {
    await ensurePdfjs();
  } catch {
    view.replaceChildren(el("iframe", { className: "pdffallback",
      src: url + "#navpanes=0&view=FitH" }));
    view._renderedUrl = url;
    return;
  }
  try {
    // Fetched here and handed to pdf.js as bytes, so a refusal's reason is in hand.
    const data = new Uint8Array(await (await fetchOrSay(url)).arrayBuffer());
    if (!latest()) return;
    await renderPdf(view, { data });
    if (latest()) view._renderedUrl = url;
  } catch (e) {
    if (!latest()) return;
    const why = e.message || "";
    view.replaceChildren(el("p", { className: "warn pdferr" },
      `${view._failText || "Could not load this document"}${why ? ": " + why : "."}`));
    view._renderedUrl = url;               // no retry loop; a new score retries
  } finally {
    if (latest() && badge) badge.remove();
  }
}


// Focusing a lyric cell asks the viewer to show that printed system, full width.
// The sidebar is too narrow to read a system in; the viewer is the space for it.
const SYSTEM_EVENT = "song-system";
const showSystem = (index) =>
  window.dispatchEvent(new CustomEvent(SYSTEM_EVENT, { detail: { index } }));
// The system last asked for, so a viewer rebuilt underneath (a tab appearing after
// the first import) comes back on it rather than on system 1.
const shownSystem = {};

// ---- the cleaned score, one system at a time, kept current (#336) -------------
// Typing lyrics is checked by looking at them on the notes, and an import changes
// a system or two. So each cleaned system's picture carries its own version in its
// URL, and an import moves on only the versions of the systems it touched: those
// are fetched again, every other picture stays exactly as it is. `seen` is the
// score fingerprint the pictures already account for, so the refresh that follows
// an import does not then reload everything for the same change; a fingerprint
// nobody announced (a re-clean, an edit in MuseScore) moves on every system.
const LYRICS_EVENT = "song-lyrics-imported";
let sysVer = { slug: null, base: "", by: {}, seen: "" };
// Set while an import is on its way: the import names what it changed when it
// lands, so a refresh that sees the new fingerprint first must not call it "all".
let lyricImporting = false;

function resetSysVersions(slug, fp) {
  sysVer = { slug, base: fp || "", by: {}, seen: fp || "" };
}

function cleanedSystemSrc(slug, n) {
  const v = sysVer.slug === slug ? (sysVer.by[n] || sysVer.base) : "";
  return `/api/songs/${encodeURIComponent(slug)}/cleaned-system/${n}?dpi=300`
    + `&v=${encodeURIComponent(v)}`;
}

// `systems` is a list of printed system numbers, or "all".
function cleanedSystemsChanged(slug, fp, systems) {
  if (sysVer.slug !== slug) resetSysVersions(slug, sysVer.seen);
  if (systems === "all") sysVer = { slug, base: fp || "", by: {}, seen: fp || "" };
  else { for (const n of systems) sysVer.by[n] = fp || ""; sysVer.seen = fp || ""; }
  window.dispatchEvent(new CustomEvent(LYRICS_EVENT, { detail: { slug, systems } }));
}

// The score moved; if nobody said which systems, all of them did.
function cleanedScoreMoved(slug, fp) {
  if (lyricImporting) return;
  if (sysVer.slug === slug && sysVer.seen === fp) return;
  cleanedSystemsChanged(slug, fp, "all");
}

// Two at a time, like the slow queue: each one can be a MuseScore run.
const swapQueue = { running: 0, waiting: [] };
function swapLimited(job) {
  const pump = () => {
    while (swapQueue.running < SLOW_AT_ONCE && swapQueue.waiting.length) {
      const next = swapQueue.waiting.shift();
      swapQueue.running++;
      next().finally(() => { swapQueue.running--; pump(); });
    }
  };
  swapQueue.waiting.push(job);
  pump();
}

// Put the picture at `src` into `box`. A picture already there stays on screen,
// under an "Updating…" note, until the new one has loaded — so nothing collapses
// or jumps while MuseScore works — and a failure leaves it there with the reason.
function swapSystemImage(box, src, n) {
  if (box._src === src) return;
  box._src = src;
  box.querySelectorAll(".liveupd, .liveerr").forEach((e) => e.remove());
  const old = box.querySelector("img");
  const note = old ? el("div", { className: "liveupd" }, "Updating…")
    : busyNote(`Engraving system ${n}…`, "busynote small");
  if (old) box.append(note); else box.replaceChildren(note);
  const current = () => box._src === src;
  swapLimited(() => fetchOrSay(src).then((r) => r.blob()).then((blob) => {
    if (!current()) return;
    const img = el("img", { className: "cmpimg", alt: `cleaned system ${n}` });
    const local = URL.createObjectURL(blob);
    return new Promise((done) => {
      img.onload = () => {
        URL.revokeObjectURL(local);
        if (current()) box.replaceChildren(img);
        done();
      };
      img.onerror = () => {
        URL.revokeObjectURL(local);
        if (current()) { box._src = null; note.remove(); box.append(el("div", { className: "liveerr" }, "The picture could not be shown.")); }
        done();
      };
      img.src = local;
    });
  }, (e) => {
    if (!current()) return;
    box._src = null; // a failed picture is asked for again the next time it is wanted
    note.remove();
    box.append(el("div", { className: "liveerr" },
      `Could not engrave system ${n}${e.message ? ": " + e.message : "."}`));
  }));
}

// ---- slow pictures: the engraved systems in Compare and Scan vs page -----------
// Each one is a MuseScore run on the server. Asked for all at once they started a
// MuseScore per system on a four-core host and arrived in whatever order those
// finished, popping in at random under an empty row (#303). So each waits in a
// placeholder that holds its place and says what it is waiting for, and they are
// fetched in reading order, two at a time.
//
// The queue belongs to the view and outlives a redraw, and that is the point: a
// scan redraws its view after every system it reads. A request already running is
// not started again and not dropped either — when it lands it fills whichever
// placeholder now stands for that picture. Stopping it instead would not stop the
// MuseScore run behind it, which carries on on the server either way, so a fresh
// pair per redraw would put four, then six, on the host.
const SLOW_AT_ONCE = 2;

function slowQueue(view) {
  const queue = view._slowQueue || (view._slowQueue = makeSlowQueue());
  queue.reset();
  return queue;
}

function makeSlowQueue() {
  let waiting = [];
  let slots = new Map();                 // src -> the placeholder showing it now
  const running = new Set();             // src of each request in flight
  const pump = () => {
    while (running.size < SLOW_AT_ONCE && waiting.length) {
      const src = waiting.shift()._src;
      if (running.has(src)) continue;
      running.add(src);
      fetchOrSay(src).then((r) => r.blob()).then(
        (blob) => slots.get(src)?._show(blob),
        (e) => slots.get(src)?._fail(e.message),
      ).finally(() => { running.delete(src); pump(); });
    }
  };
  return {
    // A redraw: the placeholders waiting are the old view's, so they go; the
    // requests running stay and land on the new placeholders.
    reset() { waiting = []; slots = new Map(); },
    push(slot) {
      if (slot._ready) return;                   // already seen: shown at once
      slots.set(slot._src, slot);
      if (!running.has(slot._src)) { waiting.push(slot); pump(); }
    },
    // The system somebody asked to look at goes next.
    front(slot) {
      const i = waiting.indexOf(slot);
      if (i > 0) { waiting.splice(i, 1); waiting.unshift(slot); }
    },
  };
}

// A picture already shown in this view is drawn straight away when the view is
// redrawn; only new ones wait. Fetched rather than left to <img>, which cannot
// read why a request failed.
function slowImage(view, src, alt, n) {
  const seen = (view._slowSeen = view._slowSeen || new Set());
  if (seen.has(src)) {
    const img = el("img", { className: "cmpimg", src, alt });
    img._ready = true;
    return img;
  }
  const slot = el("div", { className: "cmpslot" },
    busyNote(`Engraving system ${n}…`, "busynote small"));
  slot._src = src;
  slot._fail = (why) => {
    slot.className = "cmpslot err";
    slot.replaceChildren(`Could not engrave system ${n}${why ? ": " + why : "."}`);
  };
  slot._show = (blob) => {
    const img = el("img", { className: "cmpimg fresh", alt });
    const local = URL.createObjectURL(blob);
    img.onload = () => { URL.revokeObjectURL(local); seen.add(src); slot.replaceWith(img); };
    img.onerror = () => { URL.revokeObjectURL(local); slot._fail("the picture could not be shown"); };
    img.src = local;
  };
  return slot;
}

// ---- Compare: each printed system above the same system of the cleaned score ----
// Reviewing means checking one against the other, and they cannot simply be laid
// side by side: the cleaned score has twice the staves, so its systems are far
// taller. Cut both into systems and pair them up and the comparison is per line.
async function compareView(view, slug) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  // Pairing renders the whole cleaned score first, which is the slow part.
  view.replaceChildren(busyNote(
    "Building the cleaned score with MuseScore to cut it into systems…"));
  let data;
  try {
    const res = await fetch(`${P}/compare`);
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
  } catch (e) {
    view.replaceChildren(el("p", { className: "warn" },
      "Could not pair the systems" + (e.message ? ": " + e.message : ".")));
    return;
  }
  const rows = data.systems || [];
  if (!rows.length) {
    view.replaceChildren(el("p", { className: "warn" },
      "The scan's systems and the cleaned score's do not correspond, so there is "
      + "nothing to compare. Check the boundaries in the Systems tab."));
    return;
  }
  const byIndex = {};
  const queue = slowQueue(view);
  view.replaceChildren(...rows.map((r) => {
    const src = cleanedSystemSrc(slug, r.index);
    const slow = slowImage(view, src, `cleaned system ${r.index}`, r.index);
    // The cleaned picture sits in a box of its own, so an import can swap it for a
    // newer one in place (#336) whether it has arrived yet or not.
    const cleanedBox = el("div", { className: "cmpcleaned" }, slow);
    cleanedBox._src = src;
    const row = byIndex[r.index] = el("div", { className: "cmprow" },
      el("div", { className: "cmphead" },
        `System ${r.index} — measures ${r.measure_start}–${r.measure_end}`),
      el("div", { className: "cmplabel" }, "scan"),
      el("img", { className: "cmpimg", loading: "lazy",
                  src: `${P}/system/${r.index}?dpi=300`, alt: `printed system ${r.index}` }),
      el("div", { className: "cmplabel" }, "cleaned"),
      cleanedBox,
    );
    row._slow = slow;
    row._cleaned = cleanedBox;
    return row;
  }));
  for (const r of rows) queue.push(byIndex[r.index]._slow);

  // Only the systems an import touched are fetched again; the rest stay as drawn.
  view._refreshSystems = (systems) => {
    const which = systems === "all" ? Object.keys(byIndex).map(Number) : systems;
    for (const n of which) {
      const row = byIndex[n];
      if (row) swapSystemImage(row._cleaned, cleanedSystemSrc(slug, n), n);
    }
  };

  // Typing lyrics for a system should put that system in front of you, scan and
  // result together — that is the pair you are checking the words against.
  view._focusSystem = (n) => {
    const row = byIndex[n];
    if (!row) return;
    for (const el_ of Object.values(byIndex)) el_.classList.remove("cmpon");
    row.classList.add("cmpon");
    queue.front(row._slow);
    // A cleaned picture that failed is asked for again when its system is wanted.
    if (!row._cleaned._src || row._cleaned.querySelector(".cmpslot.err")) {
      row._cleaned._src = null;
      swapSystemImage(row._cleaned, cleanedSystemSrc(slug, n), n);
    }
    row.scrollIntoView({ block: "start", behavior: "smooth" });
  };
}

// ---- which homr read a system -----------------------------------------------
// Recorded and shown, and that is the whole of it (#154). An upgrade discards no
// fragment, drops no grid answer and takes no approval away; the only thing that
// re-reads a system is a person pressing one of the buttons that were already
// here. The label is what a person recognises and the commit is what actually
// decides, because `main` in a working copy means a different commit next week.
// The commit is shown and not only kept, because a working copy's label is
// stable across commits — `main — system-4` is the same words a week and forty
// commits later. Showing the label alone would put two readings of the same
// worktree under one heading with nothing to tell them apart, which is the
// ambiguity this exists to remove.
function homrShort(rec) {
  return rec && rec.commit ? rec.commit.slice(0, 7) : "";
}

function homrName(rec) {
  if (!rec || (!rec.label && !rec.commit)) return "unknown";
  const short = homrShort(rec);
  const label = rec.label || short;
  return (label.includes(short) ? label : `${label} @ ${short}`)
    + (rec.dirty ? " + uncommitted edits" : "");
}

// What actually identifies a reading, which is what the systems are grouped by.
// Two commits of one worktree are two engines however alike they read.
function homrKey(rec) {
  if (!rec || (!rec.label && !rec.commit)) return "unknown";
  return `${rec.engine || ""}|${rec.commit || ""}|${rec.dirty ? "dirty" : ""}`;
}

// null, not false, when either side cannot say — "nobody knows" is its own
// answer here and must not be shown as "a different homr".
function homrIsCurrent(rec, now) {
  if (!rec || !now || !rec.commit || !now.commit) return null;
  return rec.commit === now.commit && !rec.dirty && !now.dirty;
}

function homrGroups(st) {
  const by = new Map();
  for (const [i, rec] of Object.entries(st.homr || {})) {
    const key = homrKey(rec);
    if (!by.has(key)) by.set(key, { name: homrName(rec), ids: [] });
    by.get(key).ids.push(Number(i));
  }
  return [...by.values()].map(({ name, ids }) => [name, ids.sort((a, b) => a - b)]);
}

function homrDrift(st) {
  const all = Object.values(st.homr || {});
  return {
    stale: all.some((r) => homrIsCurrent(r, st.homr_now) === false),
    unknown: all.some((r) => homrIsCurrent(r, st.homr_now) === null),
  };
}

// ---- Scan vs page: each printed system above what the scan read off it -------
// The same idiom as Compare, one stage earlier and against the parse rather than
// the cleaned score. Cropping first is not a nicety: a whole A4 rendered small
// enough to look at cannot show a slur, which is how a confident and wrong
// reading got made here once already.
// Re-reading belongs to the Scan panel — it owns the engine picker and the log —
// but the place a person decides a system was read wrong is here, looking at the
// parse against the page. So the panel publishes its rerun and these rows call
// it, the same cross-talk `showSystem` already does in the other direction. It
// carries its slug, or a stale closure from another song would still answer.
let scanRerun = null;

function scannedView(view, song, slug) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  const st = song.scan_status || {};
  const errors = st.errors || {};
  const rows = [];
  const byIndex = {};
  const queue = slowQueue(view);
  for (let i = 1; i <= (st.systems || 0); i++) {
    const bad = errors[String(i)];
    const slow = bad ? null : slowImage(view, `${P}/scan-system/${i}?dpi=200`,
      `scanned system ${i}`, i);
    rows.push(byIndex[i] = el("div", { className: "cmprow" },
      el("div", { className: "cmphead" }, `System ${i}`),
      el("div", { className: "cmplabel" }, "page"),
      el("img", { className: "cmpimg", loading: "lazy",
                  src: `${P}/system/${i}?dpi=300`, alt: `printed system ${i}` }),
      el("div", { className: "cmplabel" }, "scan"),
      // A hole shows its reason where its music would be. Leaving the row out
      // instead is how a score quietly short of a system reads as complete.
      bad ? el("div", { className: "cmperr" }, "Could not be read: " + bad) : slow,
      // Which homr read this one, where the reading is being judged. It says so
      // and does nothing about it.
      // What the last clean made of this system, beside the picture of it.
      bad || !st.findings || !st.findings[String(i)] ? ""
        : el("div", { className: "hint scanfound" },
          `${st.findings[String(i)]} health finding(s) landed in this system`),
      bad ? "" : el("div", { className: "hint scanby" },
        "read by " + homrName((st.homr || {})[String(i)])
        + (homrIsCurrent((st.homr || {})[String(i)], st.homr_now) === false
           ? " — not the homr installed now" : "")),
      rereadRow(slug, i, bad),
    ));
    byIndex[i]._slow = slow;
  }
  view.replaceChildren(...(rows.length ? rows
    : [el("p", { className: "warn" }, "Nothing has been read yet.")]));
  for (const row of rows) if (row._slow) queue.push(row._slow);
  view._focusSystem = (n) => {
    const row = byIndex[n];
    if (!row) return;
    for (const other of Object.values(byIndex)) other.classList.remove("cmpon");
    row.classList.add("cmpon");
    if (row._slow) queue.front(row._slow);
    row.scrollIntoView({ block: "start", behavior: "smooth" });
  };
}

// One system, read again — with whichever homr the Scan panel has picked.
// A scan skips a band whose geometry has not moved, so before this the only way
// to look at a system again was to nudge its boundary: an edit to the page's own
// geometry made to trigger a side effect. What it costs is said on the row rather
// than in a dialog, because it is only a cost when the reading actually changes.
function rereadRow(slug, index, failed) {
  if (!scanRerun || scanRerun.slug !== slug) return "";
  return el("div", { className: "row cmpredo" },
    el("button", { onclick: (e) => { e.target.disabled = true; scanRerun.run([index]); } },
      `Read system ${index} again`),
    el("span", { className: "muted" },
      failed ? "It has not been read yet."
        : "A reading that comes out different discards this system's grid answers "
          + "and lapses the Review approval; one that comes out the same costs nothing."));
}

// ---- Systems: the printed-system boundaries, drawn over the page and draggable ----
// An AI proposes these off the scan; this is where they get corrected. Bounds are
// fractions of page height, so they hold at any zoom. Indices and measure ranges
// are assigned by the server on save, never by this editor.
async function systemsEditor(view, slug) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  view.replaceChildren(el("p", { className: "muted" }, "Loading pages…"));
  let data;
  try {
    data = await (await fetch(`${P}/bounds`)).json();
  } catch {
    view.replaceChildren(el("p", { className: "warn" }, "Could not load the PDF."));
    return;
  }
  let bands = (data.systems || []).map((b) => ({ page: b.page, top: b.top, bottom: b.bottom }));
  let dirty = false;

  const status = el("div", { className: "sysstatus" });
  const redrawStatus = () => {
    const n = bands.length;
    const want = data.declared || 0;
    const agree = want && n === want;
    status.replaceChildren(
      el("span", {}, `${n} system${n === 1 ? "" : "s"}`),
      want ? el("span", { className: agree ? "ok" : "warn" },
        agree ? ` — matches the score` : ` — the score declares ${want}`) : el("span"),
      dirty ? el("span", { className: "warn" }, " — unsaved") : el("span"),
    );
  };

  const save = async () => {
    const res = await fetch(`${P}/bounds`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ systems: bands }),
    });
    if (!res.ok) { alert("Save failed: " + (await res.text())); return; }
    const out = await res.json();
    bands = out.systems.map((b) => ({ page: b.page, top: b.top, bottom: b.bottom }));
    dirty = false;
    drawBands();
  };

  // Finding them is a proposal and nothing more: the bands land here unsaved and
  // dirty, exactly as if they had been dragged, so a wrong reading costs a drag
  // rather than a scan of the wrong music. "Find systems" reads the page itself
  // in well under a second a page; "Ask homr" is the slower second opinion, offered
  // only where homr is installed.
  const findNote = el("span", { className: "muted" });
  const propose = async (method, btn, who, wait) => {
    if (bands.length && !confirm(
      `Replace the ${bands.length} band(s) on this song with what ${who} finds?`)) return;
    btn.disabled = true;
    findNote.className = "muted";
    findNote.textContent = ` Looking for the systems — ${wait}…`;
    try {
      const res = await fetch(`${P}/find-systems`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method }),
      });
      if (!res.ok) throw new Error(await res.text());
      const out = await res.json();
      bands = out.systems.map((b) => ({ page: b.page, top: b.top, bottom: b.bottom }));
      dirty = true;
      findNote.textContent = ` Proposed ${bands.length} — check each one against the`
        + " page, then save.";
      drawBands();
    } catch (e) {
      findNote.className = "warn";
      findNote.textContent = " Could not find the systems: " + e.message;
    } finally {
      btn.disabled = false;
    }
  };
  const findBtn = el("button", {}, "Find systems");
  findBtn.onclick = () => propose("quick", findBtn, "the quick finder", "a moment");
  const homrBtn = el("button", { hidden: true }, "Ask homr");
  homrBtn.onclick = () => propose("homr", homrBtn, "homr", "seconds a page");
  getJSON("/api/homr-engines").then(({ engines }) => {
    homrBtn.hidden = !(engines && engines.length);
  }).catch(() => {});

  const pagesWrap = el("div", { className: "syspages" });
  // Pages are built once and kept. Rebuilding them per redraw recreates the <img>,
  // which reloads it, collapses its measured height to zero and wrecks the drag
  // geometry mid-gesture — only the overlays are redrawn.
  const pageEls = [];

  function buildPages() {
    const kids = [];
    for (let p = 1; p <= data.pages; p++) {
      const img = el("img", { src: `${P}/page/${p}?dpi=150`, alt: `page ${p}` });
      const overlay = el("div", { className: "sysoverlay" });
      const holder = el("div", { className: "syspage" }, img, overlay);
      holder.onclick = (ev) => {
        if (ev.target !== img) return;              // only empty page area adds one
        const r = img.getBoundingClientRect();
        if (!r.height) return;
        const y = (ev.clientY - r.top) / r.height;
        bands.push({ page: p, top: Math.max(0, y), bottom: Math.min(1, y + 0.15) });
        dirty = true; drawBands();
      };
      img.onload = drawBands;                        // positions need the real height
      pageEls.push({ page: p, img, overlay });
      kids.push(el("div", { className: "syspagewrap" },
        el("div", { className: "muted" }, `Page ${p}`), holder));
    }
    pagesWrap.replaceChildren(...kids);
  }

  function drawBands() {
    redrawStatus();
    for (const { page, img, overlay } of pageEls) {
      const mine = bands
        .map((b, i) => ({ b, i }))
        .filter(({ b }) => b.page === page)
        .sort((x, y) => x.b.top - y.b.top);
      overlay.replaceChildren(...mine.map(({ b, i }) => {
        const box = el("div", { className: "sysband" });
        box.style.top = `${b.top * 100}%`;
        box.style.height = `${(b.bottom - b.top) * 100}%`;
        const idx = bands.filter((o) => o.page < page ||
          (o.page === page && o.top < b.top)).length + 1;
        box.append(el("span", { className: "syslabel" }, `S${idx}`));
        box.append(el("button", {
          className: "sysdel", title: "remove this system",
          onclick: (ev) => { ev.stopPropagation(); bands.splice(i, 1); dirty = true; drawBands(); },
        }, "\u00d7"));
        for (const edge of ["top", "bottom"]) {
          box.append(el("div", {
            className: `sysgrip ${edge}`,
            onpointerdown: (ev) => startDrag(ev, img, b, edge, box),
          }));
        }
        return box;
      }));
    }
  }

  // Pointer events, not mouse: the same handler then serves a finger on the phone and
  // a mouse on the desktop, and capturing the pointer keeps a drag that wanders off
  // the grip (or off the window) attached to the band it started on.
  function startDrag(ev, img, band, edge, box) {
    ev.preventDefault(); ev.stopPropagation();
    const rect = img.getBoundingClientRect();
    if (!rect.height) return;                        // image not laid out yet
    ev.target.setPointerCapture?.(ev.pointerId);
    const move = (e) => {
      const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
      if (edge === "top") band.top = Math.min(y, band.bottom - 0.01);
      else band.bottom = Math.max(y, band.top + 0.01);
      dirty = true;
      // Move the one element being dragged; a full redraw would drop it mid-drag.
      box.style.top = `${band.top * 100}%`;
      box.style.height = `${(band.bottom - band.top) * 100}%`;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      drawBands();                                   // re-label and re-order once
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  view.replaceChildren(
    el("div", { className: "sysbar" },
      el("button", { className: "primary", onclick: save }, "Save boundaries"),
      findBtn,
      homrBtn,
      status,
      el("span", { className: "muted" },
        " Drag an edge to adjust; click the page for a new system; × removes one."),
      findNote,
    ),
    pagesWrap,
  );
  buildPages();
  drawBands();
}

// `panes` is a shared mutable array; `rebuild` re-creates the viewer (split/close).
// Each doc keeps its own scroll container mounted (lazily); switching a tab hides/shows
// them (scroll preserved). `el._refreshFp(fp)` re-renders only the cleaned previews
// after a re-clean, preserving their scroll position.
function viewer(song, slug, panes, rebuild, stage, previewSettings) {
  const tabs = viewerTabs(song, stage);
  const keys = tabs.map(([k]) => k);
  for (let i = 0; i < panes.length; i++) if (!keys.includes(panes[i])) panes[i] = keys[0];
  const refreshers = [];
  const scanRefreshers = [];
  const wakers = [];
  const selectors = [];
  const previewPausers = [];
  const previewDestroyers = [];
  const previewSyncers = [];
  const previewStarters = [];

  const slot = (i) => {
    const frames = {};                  // doc -> scrollable pdfview div (kept alive)
    const body = el("div", { className: "vbody" });
    const btns = {};
    const ensureRendered = (doc) => {
      const v = frames[doc];
      if (!v || v._systems) return;                  // the systems editor draws itself
      if (v._url === v._renderedUrl) return;
      if (v.style.display === "none") return;      // render when shown
      if (v.clientWidth > 0) mountPdf(v, v._url);
      // Wait for layout (e.g. right after a split) — but only while the pane is
      // actually on screen. On a phone the whole viewer is hidden behind the pane
      // switch, and an offscreen pane never gains width, so this would otherwise be
      // a requestAnimationFrame loop spinning forever on a battery. `_wake` picks it
      // up again when the pane comes back.
      else if (v.offsetParent) requestAnimationFrame(() => ensureRendered(doc));
    };
    const show = (doc) => {
      const previous = panes[i];
      if (previous === "preview" && doc !== "preview") frames.preview?._pause?.();
      panes[i] = doc;
      if (!frames[doc]) {
        const v = el("div", { className: "pdfview" });
        frames[doc] = v;
        body.append(v);
        if (doc === "systems") { v._systems = true; systemsEditor(v, slug); }
        else if (doc === "compare") { v._systems = true; v.className = "pdfview compare"; compareView(v, slug); }
        else if (doc === "scanned") { v._systems = true; v.className = "pdfview compare"; scannedView(v, song, slug); }
        else if (doc === "system") {
          v._systems = true;                       // draws itself, not a PDF
          v.className = "pdfview onesystem";
          // The printed system, and under it the same system of the cleaned score
          // once it has words on it: that is the pair a lyric is checked against.
          v._setSystem = (n) => {
            // Already there -- unless its cleaned picture failed, which is asked again.
            if (n === v._n && v._shownLyrics === !!song.lyrics && (!v._box || v._box._src)) return;
            v._n = shownSystem[slug] = n;
            v._shownLyrics = !!song.lyrics;
            v._box = null;
            const parts = [
              el("div", { className: "muted" }, `Printed system ${n}`),
              el("img", { src: `/api/songs/${encodeURIComponent(slug)}/system/${n}?dpi=400` }),
            ];
            if (song.has_cleaned && song.lyrics) {
              v._box = el("div", { className: "cmpcleaned" });
              parts.push(el("div", { className: "muted" }, `Cleaned system ${n}, with lyrics`), v._box);
              swapSystemImage(v._box, cleanedSystemSrc(slug, n), n);
            }
            v.replaceChildren(...parts);
          };
          v._refreshSystems = (systems) => {
            if (!v._n) return;
            if (!v._box || v._shownLyrics !== !!song.lyrics) { v._setSystem(v._n); return; }
            if (systems === "all" || systems.includes(v._n))
              swapSystemImage(v._box, cleanedSystemSrc(slug, v._n), v._n);
          };
          v._setSystem(shownSystem[slug] || 1);
        }
        else if (doc === "preview") {
          v._systems = true;
          v.className = "pdfview recordpreview";
          const P = `/api/songs/${encodeURIComponent(slug)}`;
          const preview = window.scrollPreviewPanel(P, () => {
            const settings = previewSettings();
            return settings ? settings() : {};
          }, () => song.cleaned_fingerprint);
          v._pause = () => preview._pausePreview?.();
          v._destroy = () => preview._stopPreview?.();
          v._sync = () => preview._syncPreview?.();
          v._start = () => preview._startPreview?.();
          v.append(preview);
        }
        else {
          v._url = docUrl(slug, doc, song.cleaned_fingerprint);
          if (doc !== "pdf") {
            v._busyText = "Building this score with MuseScore…";
            v._failText = "MuseScore could not build this score";
          }
          pinchZoom(v);
        }
      }
      for (const d in frames) frames[d].style.display = d === doc ? "" : "none";
      for (const k in btns) btns[k].className = k === doc ? "vtab active" : "vtab";
      zoomBox.hidden = !frames[doc]._url;   // only a PDF zooms
      ensureRendered(doc); // now visible → has width
    };
    const tabRow = tabs.map(([k, label]) => (btns[k] = el("button", {
      className: "vtab", onclick: () => show(k),
    }, label)));
    // Phone only (the stylesheet hides it above the breakpoint): page zoom is off
    // there, so this and a pinch on the score are how it is read up close. Not
    // `.vtab`: rendering_state.js remembers a `.vtab` click as the document to
    // reopen, and would press Fit again after every redraw.
    const zoomBy = (f) => () => { const v = frames[panes[i]]; if (v) zoomPdf(v, f ? (v._zoom || 1) * f : 1); };
    const zoomBox = el("span", { className: "vzoom" },
      el("button", { title: "Zoom out", "aria-label": "Zoom out", onclick: zoomBy(1 / 1.5) }, "−"),
      el("button", { title: "Zoom in", "aria-label": "Zoom in", onclick: zoomBy(1.5) }, "+"),
      el("button", { title: "Fit the width", onclick: zoomBy(0) }, "Fit"));

    if (keys.includes("system") || keys.includes("scanned")) {
      const onAsk = (ev) => {
        const n = ev.detail && ev.detail.index;
        if (!n) return;
        // Only the first pane follows the cursor, so a split can keep a second
        // document in view while the other tracks what is being typed.
        if (i !== 0) return;
        const here = frames[panes[i]];
        if (here && here._focusSystem) {
          here._focusSystem(n);       // already comparing: scroll to that pair
          return;
        }
        if (keys.includes("system")) {
          show("system");
          frames.system._setSystem(n);
        } else {
          // Before cleaning there is no "one system" tab to fall back on: the
          // pair worth showing is the band and what was read off it.
          show("scanned");
          frames.scanned._focusSystem?.(n);
        }
      };
      window.addEventListener(SYSTEM_EVENT, onAsk);
      body._cleanup = () => window.removeEventListener(SYSTEM_EVENT, onAsk);
    }
    // An import (or any change to the cleaned score) names the systems it moved;
    // only those pictures are fetched again (#336).
    const onChanged = (ev) => {
      if (!body.isConnected) { window.removeEventListener(LYRICS_EVENT, onChanged); return; }
      if (ev.detail?.slug !== slug) return;
      frames.system?._refreshSystems?.(ev.detail.systems);
      frames.compare?._refreshSystems?.(ev.detail.systems);
    };
    window.addEventListener(LYRICS_EVENT, onChanged);

    const ctrl = panes.length === 1
      ? el("button", { className: "vtab split", title: "Split view",
          onclick: () => { panes.push(keys.find((k) => k !== panes[0]) || panes[0]); rebuild(); } }, "⇆ Split")
      : el("button", { className: "vtab close", title: "Close this pane",
          onclick: () => { panes.splice(i, 1); rebuild(); } }, "✕");

    const bar = el("div", { className: "viewtabs" }, ...tabRow, el("span", { className: "spacer" }), zoomBox, ctrl);
    show(panes[i]);
    selectors.push(show);
    previewPausers.push(() => frames.preview?._pause?.());
    previewDestroyers.push(() => frames.preview?._destroy?.());
    previewSyncers.push(() => frames.preview?._sync?.());
    previewStarters.push(() => frames.preview?._start?.());
    wakers.push(() => ensureRendered(panes[i]));
    scanRefreshers.push(() => { if (frames.scanned) scannedView(frames.scanned, song, slug); });
    // Re-render only the cleaned previews; their scroll is preserved by renderPdf.
    refreshers.push((fp) => {
      for (const d in frames)
        if (d === "cleaned" || d === "cleaned_nolyrics") {
          frames[d]._url = docUrl(slug, d, fp);
          if (frames[d].style.display !== "none") ensureRendered(d);
        }
    });
    return el("div", { className: "vslot" }, bar, body);
  };

  const root = el("div", { className: "viewer" }, panes.map((_, i) => slot(i)));
  // A system read while the comparison is open must appear in it, and nothing
  // else must: redrawing reloads every crop, so it happens only when the scan
  // itself moved.
  let builtScan = `${song.scan_status?.revision}`;
  root._refreshScan = () => {
    const now = `${song.scan_status?.revision}`;
    if (now === builtScan) return;
    builtScan = now;
    scanRefreshers.forEach((f) => f());
  };
  root._refreshFp = (fp) => refreshers.forEach((f) => f(fp));
  root._wake = () => wakers.forEach((f) => f());
  root._pausePreview = () => previewPausers.forEach((pause) => pause());
  root._destroyPreview = () => previewDestroyers.forEach((destroy) => destroy());
  root._syncPreview = () => previewSyncers.forEach((sync) => sync());
  // Only the first pane: it is the one the panel's Preview button brings forward,
  // and a split showing the preview twice should not prepare it twice.
  root._startPreview = () => previewStarters[0]?.();
  root._showFirst = (doc) => {
    if (keys.includes(doc) && selectors[0]) selectors[0](doc);
  };
  return root;
}

function appendLog(line, isErr) {
  if (!logBox) return;
  logBox.append(el("div", { className: isErr ? "err" : "" }, line));
  logBox.scrollTop = logBox.scrollHeight;
}

// Live-updating line for upload percentage: rewrite the last row if it's a
// progress line, otherwise start a new one (so 0→100% doesn't spam the log).
function updateProgress(line) {
  if (!logBox) return;
  const last = logBox.lastElementChild;
  if (last && last.classList.contains("progress")) last.textContent = line;
  else logBox.append(el("div", { className: "progress" }, line));
  logBox.scrollTop = logBox.scrollHeight;
}

// The most recently started of these jobs: one panel log shows whichever ran last.
function latestJob(jobs, kinds) {
  return kinds.map((kind) => jobs?.[kind]).filter(Boolean)
    .sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0];
}

function makeLog(job) {
  logBox = el("div", { className: "log" });
  for (const entry of job?.logs || []) {
    const cls = entry.type === "error" ? "err" : entry.type === "progress" ? "progress" : "";
    logBox.append(el("div", { className: cls }, entry.line));
  }
  logBox.scrollTop = logBox.scrollHeight;
  return logBox;
}

function verificationView(summary) {
  if (!summary) return el("p", { className: "hint" }, "Verification has not run.");
  const icon = { passed: "✓", warning: "⚠", stale: "⚠", not_checked: "—" };
  const row = (name, result) => el("li", { className: `check ${result?.status || "not_checked"}` },
    `${icon[result?.status] || "—"} ${name}: ${result?.detail || "Not checked."}`);
  const media = summary.media || {};
  const files = Object.entries(media.files || {}).map(([part, result]) =>
    el("li", { className: `check file ${result.status || "not_checked"}` },
      `${icon[result.status] || "—"} ${part}: ${result.detail}`));
  return el("div", { className: "verify" },
    el("h3", {}, "Verification"),
    el("p", { className: "hint" },
      `Expected parts: ${(summary.expected_parts || []).join(", ") || "not available"}; `
      + `printed systems: ${summary.systems || "not available"}.`),
    el("ul", {},
      row("Health", summary.health),
      row("Source notes", summary.notes),
      row("Opens in MuseScore", summary.musescore),
      row("Lyrics", summary.lyrics),
      row("Rendered files", media),
      ...files),
    summary.render_error ? el("div", { className: "banner err" }, "Render error: " + summary.render_error) : "");
}

const CHECK_ICON = { passed: "✓", warning: "⚠", stale: "⚠", not_checked: "—" };

function compactCheck(name, result) {
  const status = result?.status || "not_checked";
  return el("li", { className: `compact-check ${status}` },
    el("span", { className: "compact-icon", "aria-hidden": "true" }, CHECK_ICON[status] || "—"),
    el("strong", {}, name),
    el("span", { className: "compact-detail" }, result?.detail || "Not checked."));
}

function reviewReadiness(summary) {
  if (!summary) return { tone: "attention", title: "Needs attention" };
  const core = [summary.notes, summary.lyrics, summary.health];
  const media = summary.media;
  if (core.some((result) => ["stale", "not_checked"].includes(result?.status))) {
    return { tone: "attention", title: "Needs attention" };
  }
  if (core.some((result) => result?.status === "warning")
      || ["warning", "stale"].includes(media?.status)) {
    return { tone: "recommended", title: "Review recommended" };
  }
  return { tone: "ready", title: "Ready to approve" };
}

function compactReview(summary) {
  const readiness = reviewReadiness(summary);
  return el("section", { className: `compact-review ${readiness.tone}` },
    el("div", { className: "review-state" }, readiness.title),
    el("ul", {},
      compactCheck("Notes", summary?.notes),
      compactCheck("MuseScore", summary?.musescore),
      compactCheck("Lyrics", summary?.lyrics),
      compactCheck("Health", summary?.health),
      compactCheck("Videos", summary?.media)));
}

// ---- per-stage panels ----------------------------------------------------
function renderPanel(panel, view, song, slug, refresh, actions) {
  logBox = null;
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  if (view === "register") return panelRegister(panel, song, P, refresh);
  if (view === "scan") return panelScan(panel, song, P, refresh, actions);
  if (view === "clean") return panelClean(panel, song, slug, P, refresh);
  if (view === "fix") return panelFix(panel, song, P, refresh);
  if (view === "lyrics") return panelLyrics(panel, song, P, refresh);
  if (view === "review") return panelReview(panel, song, P, refresh, actions);
  if (view === "record") return panelRecord(panel, song, P, refresh, actions);
  if (view === "upload") return panelUpload(panel, song, P, refresh);
}

// The Scan panel. It has one job the other panels do not: it has to stop a parse
// that looks tidy from becoming a practice track. So the stage never advances on
// its own — the operator compares the parse against the page system by system and
// then says, once for the song, that it is right (#99).
function panelScan(panel, song, P, refresh, actions) {
  const st = song.scan_status || {};
  const job = song.jobs?.scan;
  const running = !!(song.scanning || job?.status === "running");
  const gaps = st.pages_without_bands || [];
  const openScanned = () => actions?.openDoc?.("scanned");
  panel.append(el("h2", {}, "Scan"),
    el("p", { className: "sub" },
      "Read the score off the PDF, one printed system at a time."));

  if (!song.has_pdf) {
    panel.append(el("p", { className: "hint" },
      "This song has no PDF, so there is nothing to read."));
    return;
  }

  // Bounds first, always: they are drawn by hand and nothing proposes them, so
  // the panel says what is missing and the Scan button waits for it.
  const ready = st.systems && !gaps.length;
  panel.append(el("div", { className: ready ? "banner" : "banner err" },
    ready ? `${st.systems} printed system(s) marked.`
      : !st.systems
      ? "No printed systems marked yet. Draw a band around each system in the Systems tab — the scan reads exactly those bands."
      : `Page(s) ${gaps.join(", ")} have no systems marked. Every page has to be marked, or its music is simply never read.`,
    " ",
    el("button", { onclick: () => actions?.openDoc?.("systems") }, "Open the Systems editor")));

  if (st.systems)
    panel.append(el("p", {}, `${st.read} of ${st.systems} system(s) read.`
      + (st.holes?.length ? ` Still to read: ${st.holes.join(", ")}.` : "")));
  // Already sentences: the server words them (`scan.said`).
  for (const line of song.scan_discarded || [])
    panel.append(el("div", { className: "banner" }, line));

  // Which homr reads the page. Only offered when this host has more than one
  // installed (HOMR_BRANCH=... scripts/install-homr.sh) — a picker with a single
  // entry is furniture. The choice lasts this scan run; what it resolved to is
  // recorded on each fragment it reads and shown under "Read by". Two engines are
  // compared by reading a system with one, then the other, and looking at both
  // against the page.
  let engine = null;
  const engineRow = el("div", { className: "row" });
  panel.append(engineRow);
  getJSON("/api/homr-engines").then(({ engines }) => {
    if (!engines || engines.length < 2) return;
    const sel = el("select", { onchange: () => { engine = sel.value; } },
      ...engines.map((e) => el("option", { value: e.key },
        e.label + (e.default ? " (default)" : ""))));
    engine = sel.value;
    engineRow.append(el("label", { className: "hint" }, "Read with"), sel);
  }).catch(() => {});
  panel.append(homrBox());

  const rerun = async (systems) => {
    appendLog(systems ? "Re-reading system(s) " + systems.join(", ") + "…"
                      : "Starting scan…");
    const body = Object.assign(systems ? { systems } : {}, engine ? { engine } : {});
    try { await postJSON(`${P}/scan`, body); refresh(); }
    catch (e) { appendLog(e.message, true); }
  };
  // The compare rows re-read from here, so they get the picked engine and the log
  // without a second copy of either.
  scanRerun = { slug: song.slug, run: rerun };
  // Only offered while there is something to read: a band already read at its
  // current geometry is skipped, so a button on a finished scan would say it was
  // going to do something and then do nothing.
  const outstanding = !st.read || st.holes?.length;
  const runBtn = el("button", { className: "primary", disabled: !ready || running,
    onclick: async () => { runBtn.disabled = true; await rerun(null); } },
    st.read ? "Read the remaining systems" : "Scan the score");
  panel.append(el("div", { className: "row" }, outstanding ? runBtn : "",
    st.read ? el("button", { disabled: running, onclick: openScanned },
                 "Compare with the page") : ""));

  // A hole is blocking and it is retried on its own: twenty homr runs is twenty
  // chances to fail, and losing the nineteenth must not cost the eighteen that
  // worked.
  if (st.holes?.length && st.read) {
    panel.append(el("h3", {}, "Systems still to read"));
    for (const index of st.holes) {
      const why = (st.errors || {})[String(index)];
      panel.append(el("div", { className: "banner err scanhole" },
        el("div", {}, `System ${index}`
          + (why ? " could not be read: " + why : " has not been read yet.")),
        el("div", { className: "row" },
          el("button", { disabled: running, onclick: () => rerun([index]) },
            "Read system " + index + " again"),
          el("button", { onclick: () => { openScanned(); showSystem(index); } },
            "Look at it"))));
    }
  }

  // A system that read fine is skipped by the next scan, because its band has not
  // moved — so without this the only way to look at it again with the other engine
  // was to drag its boundary, which is editing the page's geometry to provoke a
  // side effect. Numbers rather than a list of rows: the looking is done in
  // `Scan vs page`, which offers the same thing on each system it shows.
  const done = [];
  for (let i = 1; i <= (st.systems || 0); i++)
    if (!(st.holes || []).includes(i)) done.push(i);
  if (st.read && done.length) {
    panel.append(el("h3", {}, "Read a system again"),
      // Directly above the buttons it is about: the point of carrying the findings
      // back to this stage is that acting on them here costs one press.
      scanFindingsHint(st),
      el("p", { className: "hint" },
        "Forces a re-read even though the band has not moved — for trying another "
        + "engine, or a system that came back wrong. A reading that comes out "
        + "different discards that system's grid answers and lapses the Review "
        + "approval; one that comes out the same costs nothing."),
      el("div", { className: "row" }, ...done.map((i) =>
        el("button", { disabled: running, onclick: () => rerun([i]) }, String(i)))),
      // The whole score through the same per-system path, so each system still
      // only costs anything when its reading comes out different.
      el("div", { className: "row" },
        el("button", { disabled: running, onclick: () => rerun(done) },
          "Read all systems again")));
  }

  if (st.read) panel.append(scanProvenance(st));

  if (running)
    panel.append(el("div", { className: "banner" },
      "● Scanning… recent messages are saved below."));
  else if (job?.status === "failed")
    panel.append(el("div", { className: "banner err" }, "Last scan failed: " + job.error));

  // No gate (#281): a whole score moves the song on by itself, and the bar-by-bar
  // checking happens where it can be done — the ⚠ marks, Fix and Review.
  if (st.complete && !running) panel.append(scanDone(st, actions));

  panel.append(makeLog(job));
}

// Which homr read the systems of this scan. Said, never acted on: a song whose
// systems were read by an older homr is not re-read for it, because the crop is
// the same crop and the parse is still the parse (#154). What it buys is the
// session #129 spent diagnosing a defect that had already been fixed, in a
// fragment nothing said was old.
function scanProvenance(st) {
  const groups = homrGroups(st);
  if (!groups.length) return "";
  const { stale, unknown } = homrDrift(st);
  return el("section", { className: "scanby" },
    el("h3", {}, "Read by"),
    ...groups.map(([name, ids]) =>
      el("p", { className: "hint" }, `${name} — system(s) ${ids.join(", ")}`)),
    st.homr_now ? el("p", { className: "hint" },
      "Installed now: " + homrName(st.homr_now) + ".") : "",
    stale || unknown
      ? el("p", { className: "warn" },
        (stale ? "Some systems were read by a different homr than the one installed now. " : "")
        + (unknown ? "Some were read before the app recorded which homr read them. " : "")
        + "Nothing has been discarded for it. Read a system again above if you want "
        + "this homr's reading of it.")
      : "");
}

// Where the last clean's findings fell, said on the screen that can re-read a
// system. Once a clean has happened the damage has system numbers, and this is the
// one place where acting on them costs a single button.
function scanFindingsHint(st) {
  const byIndex = st.findings;
  if (!byIndex) return "";
  const worst = Object.entries(byIndex).filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!worst.length) return "";
  const total = worst.reduce((sum, [, n]) => sum + n, 0);
  const named = worst.slice(0, 3).map(([i, n]) => `${i} (${n})`).join(", ");
  return el("p", { className: "hint scanfindings" },
    `The last clean of this reading found ${total} health finding(s). Most of them are `
    + `in system(s) ${named} — read those against the page first.`);
}

function scanDone(st, actions) {
  return el("div", { className: "banner good scandone" },
    el("div", {}, `All ${st.systems} systems read — the song is on Clean. Bars homr `
      + "was unsure of are marked ⚠ and listed in Fix."),
    actions?.selectStage ? el("div", { className: "row" },
      el("button", { className: "primary", onclick: () => actions.selectStage("clean") },
        "Go to Clean")) : "");
}

function panelRegister(panel, song, P, refresh) {
  const uploaded = !!(song.record?.uploads?.length);
  const nameInput = el("input", { value: song.name, style: "width:100%" });
  const saveBtn = el("button", { className: "primary", onclick: async () => {
    const name = nameInput.value.trim();
    if (!name || name === song.name) return;
    saveBtn.disabled = true;
    appendLog("Renaming…" + (uploaded ? " (updating YouTube titles in background)" : ""));
    try {
      const fresh = await postJSON(`${P}/rename`, { name });
      Object.assign(song, fresh);
      crumb.textContent = "› " + song.name;
      appendLog("Renamed to " + song.name);
      refresh();
    } catch (e) { saveBtn.disabled = false; appendLog(e.message, true); }
  }}, "Save name");

  panel.append(
    el("h2", {}, "Start"),
    el("label", {}, "Display name"),
    nameInput,
    el("div", { className: "row" }, saveBtn,
      el("span", { className: "hint" }, uploaded
        ? "Already on YouTube — titles will be updated too."
        : "The folder name stays the same.")),
    el("p", { className: "sub", style: "margin-top:16px" }, "Sources for this song."),
    el("p", {}, `Score: ${song.sources?.xml || "—"}`),
    el("p", {}, `PDF: ${song.sources?.pdf || "— (none)"}`),
    el("p", {}, `Mode: ${song.mode}`),
    el("p", { className: "hint" }, "Go to the Clean step to build the score."),
    makeLog());
}

async function panelClean(panel, song, slug, P, refresh) {
  const cleanJob = song.jobs?.clean;
  panel.append(el("h2", {}, "Clean"),
    el("p", { className: "sub" }, song.mode === "per-system"
      ? "Name each staff's voices per system, then build."
      : "Split the shared-staff voices into one staff per part."));

  // Mode toggle — switchable any time (e.g. a normal score turns out to need per-system).
  const modeChk = el("input", { type: "checkbox", checked: song.mode === "per-system" });
  modeChk.onchange = async () => {
    modeChk.disabled = true;
    try { await postJSON(`${P}/mode`, { mode: modeChk.checked ? "per-system" : "normal" }); refresh(); }
    catch (e) { modeChk.disabled = false; appendLog(e.message, true); }
  };
  panel.append(el("div", { className: "row" }, modeChk,
    el("span", {}, "Per-system mode (staves change parts between systems)")));

  const cleanLabel = song.has_cleaned ? "Re-clean (discards manual edits)" : "Run clean";

  if (song.mode === "per-system") {
    const holder = el("div", {}, el("p", { className: "hint" }, "Loading systems…"));
    panel.append(holder);
    try {
      const { grid } = await getJSON(`${P}/systems`);
      holder.replaceChildren(
        el("p", { className: "hint fallbackhint" },
          "S1b sings S1's notes wherever it has none of its own; the same goes for any part name plus one small letter."),
        ...grid.map((sys) => sysBlock(sys)));

      // Roll a staff's answer forward: an empty field shows the previous system's
      // answer for the same staff as a faint placeholder (and inherits it at clean).
      // A cell holding "-" says the staff is silent from there on, so it clears the
      // carry instead of setting it — same rule the backend applies (per_system.CLEARED).
      const CLEARED = "-";
      const inputs = [...holder.querySelectorAll("input[data-sys]")];
      const byStaff = () => {
        const m = {};
        for (const inp of inputs) (m[inp.dataset.staff] ||= []).push(inp);
        for (const sid in m) m[sid].sort((a, b) => a.dataset.sys - b.dataset.sys);
        return m;
      };
      // Lines with no answer: a slot holding a name or "-" (silent on purpose) answers
      // its line, an empty one ("A1,") does not — the same count the server reports
      // by (per_system.dropped_voices). A staff with no name at all is "unset" instead.
      const underNamed = (inp, carry) => {
        if (!carry) return 0;
        const slots = carry.split(",").map((n) => n.trim());
        if (!slots.some((n) => n && n !== CLEARED)) return 0;
        let answered = 0;
        for (let i = 0; i < +inp.dataset.lines; i++) if (slots[i]) answered++;
        return +inp.dataset.lines - answered;
      };
      const cascade = () => {
        for (const list of Object.values(byStaff())) {
          let carry = "";
          for (const inp of list) {
            const v = inp.value.trim();
            if (v === CLEARED) carry = "";
            else if (v) carry = v;
            else inp.placeholder = carry || inp.dataset.hint;
            // flag staves that will be dropped (cleared, or no value and nothing to inherit)
            inp.classList.toggle("unset", (!v || v === CLEARED) && !carry);
            // ...and staves named with fewer parts than the lines they carry here: the
            // rebuild takes one name per line, top first, so the rest are dropped (#330).
            // Usually an answer carried over from a system where the staff had one line.
            const lost = underNamed(inp, carry);
            inp.classList.toggle("undernamed", lost > 0);
            const note = inp.parentElement.querySelector(".undernote");
            if (note) note.textContent = lost > 0
              ? `${inp.dataset.lines} voices here, ${inp.dataset.lines - lost} answered — `
                + (lost === 1 ? "one is" : `${lost} are`) + " dropped"
              : "";
          }
        }
      };
      const effectiveBefore = (staff, system) => {
        let carry = "";
        for (const inp of byStaff()[staff] || []) {
          if (+inp.dataset.sys >= system) break;
          const value = inp.value.trim();
          if (value === CLEARED) carry = "";
          else if (value) carry = value;
        }
        return carry;
      };
      holder.querySelectorAll("button[data-reuse]").forEach((button) => {
        button.onclick = () => {
          const start = +button.dataset.reuse;
          for (let si = start; si < grid.length && grid[si].can_reuse_previous; si++) {
            for (const inp of inputs.filter((candidate) => +candidate.dataset.sys === si)) {
              const suggested = effectiveBefore(inp.dataset.staff, si);
              if (!inp.value.trim() && suggested) inp.value = suggested;
            }
          }
          cascade();
        };
      });
      // staves left unnamed (would be dropped from the result)
      const unnamed = () => {
        const out = [];
        for (const list of Object.values(byStaff())) {
          let carry = "";
          for (const inp of list) {
            const v = inp.value.trim();
            if (v === CLEARED) carry = "";
            else if (v) carry = v;
            if ((!v || v === CLEARED) && !carry)
              out.push(`staff ${inp.dataset.staff} · system ${+inp.dataset.sys + 1}`);
          }
        }
        return out;
      };
      const dropping = () => inputs.filter((inp) => inp.classList.contains("undernamed"))
        .map((inp) => `staff ${inp.dataset.staff} · system ${+inp.dataset.sys + 1}`
          + ` — ${inp.parentElement.querySelector(".undernote").textContent}`);
      inputs.forEach((inp) => (inp.oninput = cascade));
      cascade();

      const collect = () => {
        const answers = {};
        panel.querySelectorAll("input[data-sys]").forEach((inp) => {
          const si = inp.dataset.sys, sid = inp.dataset.staff;
          (answers[si] ||= {})[sid] = inp.value.trim();
        });
        return answers;
      };
      const save = () => api(`${P}/systems`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify(collect()),
      });

      const saveBtn = el("button", { onclick: async () => {
        try { await save(); saveBtn.textContent = "Saved ✓";
              setTimeout(() => { saveBtn.textContent = "Save assignments"; }, 1500); }
        catch (e) { appendLog(e.message, true); }
      }}, "Save assignments");
      const runBtn = el("button", { className: "primary", onclick: async () => {
        const miss = unnamed();
        if (miss.length && !confirm(
            `${miss.length} staff slot(s) have no voice names and will be DROPPED from the result:\n\n`
            + miss.slice(0, 12).join("\n") + (miss.length > 12 ? `\n…and ${miss.length - 12} more` : "")
            + "\n\nClean anyway?")) return;
        const lost = dropping();
        if (lost.length && !confirm(
            `${lost.length} staff slot(s) have more voices than names, and the unnamed voices will be DROPPED (a chord's extra notes are kept in the lowest named part's chord):\n\n`
            + lost.slice(0, 12).join("\n") + (lost.length > 12 ? `\n…and ${lost.length - 12} more` : "")
            + "\n\nName each line (e.g. A1, A1b) to give it a part. Clean anyway?")) return;
        runBtn.disabled = true;
        appendLog("Saving assignments and cleaning…");
        try { await save(); await postJSON(`${P}/clean`, {}); }
        catch (e) { runBtn.disabled = false; appendLog(e.message, true); }
      }}, cleanLabel);
      panel.append(el("div", { className: "row" }, saveBtn, runBtn));
    } catch (e) {
      holder.replaceChildren(el("p", { className: "err" }, "Could not read systems: " + e.message));
    }
  } else {
    const runBtn = el("button", { className: "primary", onclick: async () => {
      runBtn.disabled = true;
      appendLog("Starting clean…");
      await postJSON(`${P}/clean`, {});
    }}, cleanLabel);
    panel.append(el("div", { className: "row" }, runBtn));
  }
  if (cleanJob?.status === "running")
    panel.append(el("div", { className: "banner" }, "● Cleaning… recent messages are saved below."));
  else if (cleanJob?.status === "failed")
    panel.append(el("div", { className: "banner err" }, "Last clean failed: " + cleanJob.error));
  panel.append(makeLog(cleanJob));
  if (song.has_cleaned) appendLog("A cleaned score exists. Re-cleaning will overwrite it.");
}

function sysBlock(sys) {
  const rows = sys.staves.map((st) =>
    el("tr", {},
      el("td", {}, "staff " + st.staff_id),
      el("td", {}, String(st.voices)),
      el("td", { className: "stsum" }, st.summary),
      el("td", {}, el("input", {
        value: st.answer || "", placeholder: st.voices > 1 ? "e.g. T1, T2 (- = silent)" : "e.g. T1 (- = silent)",
        "data-sys": sys.system, "data-staff": st.staff_id, "data-voices": st.voices,
        "data-lines": st.lines ?? st.voices,
        "data-hint": st.voices > 1 ? "e.g. T1, T2 (- = silent)" : "e.g. T1 (- = silent)",
      }), el("div", { className: "undernote" }))));
  const reuse = sys.can_reuse_previous
    ? el("button", { "data-reuse": sys.system }, "Reuse previous assignments through matching systems")
    : "";
  return el("div", { className: "sysblock" },
    el("div", { className: "row syshead" },
      el("h4", {}, `System ${sys.system + 1} — measures ${sys.measure_start}–${sys.measure_end}`), reuse),
    el("table", { className: "grid" },
      el("tr", {}, el("th", {}, "Staff"), el("th", {}, "Voices"), el("th", {}, "Content"), el("th", {}, "Part names")),
      rows));
}

function panelFix(panel, song, P, refresh) {
  panel.append(el("h2", {}, "Fix"),
    el("p", { className: "sub" }, "OCR damage the auto-fixers couldn't repair. Fix in MuseScore, save, and it re-checks automatically."));
  problemList(panel, song, P, refresh);
  panel.append(el("div", { className: "row" },
    el("button", { className: "primary", onclick: () => postJSON(`${P}/open-score`) }, "Open in MuseScore"),
    el("button", { onclick: async () => { await postJSON(`${P}/rescan`); refresh(); } }, "Re-check now")));
  panel.append(scoreFileTransfer(song, P, refresh));
  slurRecorder(panel, song, P, refresh);
}

// Every problem the score has, one row per bar and part, with whatever there is to
// choose between beside the page (#290). The server builds the rows — red marks read
// live off the score, health findings, sentences in fixes.json, homr's other readings
// of a bar as whole bars (lengths and pitches together, and its second reading), and
// a slur the scan ran between two singers — so the panel decides nothing about the
// music. A tap applies the answer to the score and records it in fixes.json, so a
// re-clean keeps it.
const NOTE_KIND = { mark: "red mark", "fixes.json": "fixes.json — not applied automatically",
  "musescore-check": "fixes.json — not applied automatically", scan: "fixes.json — not applied automatically" };

// Which staff of the cropped system a row's part was printed on, and which voice of
// that staff, counted from the top (#310): "staff 1 of 4, lower voice".
function staffPlace(row) {
  if (!row.staff_in_system) return "";
  const voices = row.voices_on_staff;
  const voice = voices === 1 ? "only voice"
    : voices === 2 ? (row.voice_on_staff === 1 ? "upper voice" : "lower voice")
    : `voice ${row.voice_on_staff} of ${voices} from the top`;
  return `staff ${row.staff_in_system} of ${row.staves_in_system}, ${voice}`;
}

// Where the Fix panel was when a tap redrew it (#329). A pick or a Dismiss redraws the
// whole panel, and while the rows are fetched again the panel is short, so the
// browser clamps its scroll to the bottom of what is left and keeps it there when the
// cards arrive above. So the card tapped is remembered with the cards after it, and
// once the rows are back the first of them still open is put where the tapped one
// stood — the next card slides into its place.
// A tap is not the only redraw: the score changing on disk sends a `state` ping, and
// after a pick the file watcher can send one too, a moment after the tap's own
// redraw. Every refresh therefore asks the panel to remember the card at the top of
// what it shows (`panel._keepPlace`) unless a tap already said where to land.
let fixPlace = null;   // {slug, rows: [row ids], offset, scrollTop}

function problemList(panel, song, P, refresh) {
  const box = el("div", { className: "problems" });
  const scroller = () => box.closest(".panel") || panel;
  const remember = (rowId) => {
    const sc = scroller();
    const cards = [...box.querySelectorAll(".issue.problem")];
    const at = cards.findIndex((c) => c.dataset.row === rowId);
    const card = cards[at];
    fixPlace = { slug: song.slug, scrollTop: sc.scrollTop,
      rows: at < 0 ? [] : cards.slice(at).map((c) => c.dataset.row),
      offset: card ? card.getBoundingClientRect().top - sc.getBoundingClientRect().top : 0 };
  };
  // The card at the top of what is on screen, for a redraw nobody tapped for.
  panel._keepPlace = () => {
    if (fixPlace?.slug === song.slug || !box.isConnected) return;
    const sc = scroller();
    const top = sc.getBoundingClientRect().top;
    const cards = [...box.querySelectorAll(".issue.problem")];
    const at = cards.findIndex((c) => c.getBoundingClientRect().bottom > top);
    // `untapped`: with no card in view (the reader is below the list, at the slur
    // recorder say), the scroll position itself is put back — the "decided" summary
    // standing in for the card is only right after a tap.
    fixPlace = { slug: song.slug, scrollTop: sc.scrollTop, untapped: true,
      rows: at < 0 ? [] : cards.slice(at).map((c) => c.dataset.row),
      offset: at < 0 ? 0 : cards[at].getBoundingClientRect().top - top };
  };
  const restore = () => {
    const place = fixPlace;
    fixPlace = null;
    if (!place || place.slug !== song.slug) return;
    const sc = scroller();
    const cards = new Map([...box.querySelectorAll(".issue.problem")].map((c) => [c.dataset.row, c]));
    const target = place.rows.map((id) => cards.get(id)).find(Boolean)
      || (place.untapped ? null : box.querySelector(".readdone"));
    const place_it = () => {
      if (!target.isConnected) return;
      sc.scrollTop += target.getBoundingClientRect().top - sc.getBoundingClientRect().top - place.offset;
    };
    if (!target) {
      sc.scrollTop = Math.min(place.scrollTop, sc.scrollHeight - sc.clientHeight);
      return;
    }
    place_it();
    // The page crops and engraved options load after the cards, and one above the
    // card grows the list under it. Not every browser holds the view still for that
    // (Safari does not), so put the card back as each picture lands — until the
    // person scrolls or taps, after which where they are is theirs.
    const stop = () => {
      box.querySelectorAll("img").forEach((i) => i.removeEventListener("load", place_it));
      ["wheel", "touchstart", "pointerdown", "keydown"].forEach((t) => sc.removeEventListener(t, stop));
    };
    box.querySelectorAll("img").forEach((i) => { if (!i.complete) i.addEventListener("load", place_it); });
    ["wheel", "touchstart", "pointerdown", "keydown"].forEach((t) => sc.addEventListener(t, stop, { passive: true }));
    setTimeout(stop, 5000);
  };
  panel.append(box);
  if (!song.has_cleaned) {
    box.append(el("p", { className: "empty" }, "Clean the score first."));
    box.dataset.loaded = "1";
    return;
  }
  box.append(el("p", { className: "hint" }, "Reading the score…"));
  const problem = el("p", { className: "lyerr readerr" });
  const pick = async (row, choice, letter, buttons) => {
    problem.textContent = "";
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const fresh = await postJSON(`${P}/problems/pick`, { choice: choice.id, kind: choice.kind, letter });
      Object.assign(song, fresh);
      remember(row.id);
      refresh();
    } catch (e) {
      fixPlace = null;
      problem.textContent = e.message;
      buttons.forEach((b) => { b.disabled = false; });
    }
  };
  const where = (row) => row.measure ? `Bar ${row.measure}${row.part ? ", " + row.part : ""}` : "Note";
  const decidedText = (row, c) => {
    const d = c.decision;
    if (d.none) return `${where(row)}: none of these — fix it in MuseScore`;
    // A bar a recorded fix already wrote is not asked about again (#368).
    if (d.answered) return `${where(row)}: answered by fixes.json (${d.answered.kind})`
      + (d.answered.why ? ` — ${d.answered.why}` : "");
    const word = { slur: "slur answer", repeat: "repeat answer", volta: "bracket answer" }[c.kind] || "reading";
    if (d.picked === "earlier") return `${where(row)}: picked before whole bars were offered`;
    const opt = c.options.find((o) => o.letter === d.picked);
    return `${where(row)}: ${word} ${d.picked}` + (opt && opt.label ? ` (${opt.label})` : "");
  };
  const choiceBlock = (row, c) => {
    const buttons = [];
    const options = c.options.map((o) => {
      const b = el("button", { className: "readopt" + (o.svg ? "" : " readtext"),
        onclick: () => pick(row, c, o.letter, buttons) },
        el("span", { className: "readletter" }, o.letter),
        o.label ? el("span", { className: "readlabel" + (o.line_of ? " readswap" : "") }, " " + o.label) : "",
        o.current ? el("span", { className: "hint" }, " as read now") : "",
        o.svg ? el("img", { className: "readsvg", loading: "lazy", alt: `option ${o.letter}`,
          src: `${P}/${o.svg.split("/").map(encodeURIComponent).join("/")}` }) : "");
      buttons.push(b);
      return b;
    });
    // A whole-bar choice can offer many bars; the likeliest few show first and the
    // rest wait behind "More", so a card stays short on a phone.
    const opts = el("div", { className: "readopts" }, ...options);
    const block = el("div", { className: "readpick", "data-offer": c.id },
      el("div", { className: "readtitle" }, c.title));
    // What fixes.json already says about this bar: a pick can undo a slur or tie
    // here, or give this part the line a fix put on the part beside it (#368).
    if (c.fixes && c.fixes.length) {
      block.append(el("div", { className: "readwarn" },
        el("strong", {}, `fixes.json already changes bar ${c.measure}:`),
        el("ul", {}, ...c.fixes.map((f) => el("li", {},
          `${f.part} (${f.kind})${f.why ? ": " + f.why : ""}`))),
        "Picking here may undo or contradict it."));
    }
    block.append(opts);
    if (c.shown && options.length > c.shown) {
      const hidden = options.slice(c.shown);
      hidden.forEach((b) => { b.hidden = true; });
      const more = el("button", { className: "readmore", onclick: () => {
        hidden.forEach((b) => { b.hidden = false; });
        more.remove();
      } }, `More (${hidden.length})`);
      block.append(el("div", { className: "row" }, more));
    }
    if (c.can_decline) {
      const none = el("button", { className: "readnone", onclick: () => pick(row, c, "none", buttons) },
        "None of these");
      buttons.push(none);
      block.append(el("div", { className: "row" }, none,
        el("span", { className: "hint" }, "keeps it as read and the red mark; fix the bar in MuseScore")));
    }
    return block;
  };
  // The printed system with the bar and staff the row is about boxed (#368): the
  // crop shows every staff and bar of the line. The box comes from the page itself
  // and is drawn only as far as it can be found — the staff alone when the bar's
  // barlines cannot all be told from stems, nothing when the staves cannot.
  const cropWithMark = (row) => {
    const wrap = el("div", { className: "cropwrap" },
      el("img", { className: "readcrop", loading: "lazy", alt: `printed system ${row.system}`,
        src: `${P}/system/${row.system}?dpi=200` }));
    if (!row.staff_in_system) return wrap;
    const q = new URLSearchParams({ staff: row.staff_in_system, staves: row.staves_in_system, dpi: 200 });
    if (row.bar_in_system) { q.set("bar", row.bar_in_system); q.set("bars", row.bars_in_system); }
    getJSON(`${P}/system/${row.system}/where?${q}`).then(({ box }) => {
      if (!box || !wrap.isConnected) return;
      const pct = (v) => `${(v * 100).toFixed(2)}%`;
      wrap.append(el("div", { className: "cropmark" + (box.bar ? "" : " staffonly"),
        title: box.bar ? `bar ${row.bar_in_system}, ${row.part}` : `${row.part}'s staff`,
        style: `top:${pct(box.top)};height:${pct(box.bottom - box.top)};`
          + `left:${pct(box.left)};width:${pct(box.right - box.left)}` }));
    }).catch(() => {});
    return wrap;
  };
  // Said once the rows are in, so whoever reads the panel (a test, say) can tell an
  // empty list from one still on its way.
  const loaded = () => { box.dataset.loaded = "1"; };
  getJSON(`${P}/problems`).then(({ rows }) => {
    // Redrawn again while this was on its way: the newer list owns the place.
    if (!box.isConnected) return;
    box.replaceChildren();
    loaded();
    const open = rows.filter((r) => r.notes.length || r.choices.some((c) => !c.decision));
    const decided = rows.flatMap((r) => r.choices.filter((c) => c.decision).map((c) => decidedText(r, c)));
    if (!open.length) box.append(el("p", { className: "empty" }, "✓ No issues. Ready for lyrics."));
    else box.append(el("p", { className: "sub problemcount" },
      `${open.length} place(s) to check against the page. Tap the letter the page prints; it is applied to the score and kept in fixes.json, so a re-clean keeps it.`));
    for (const row of open) {
      // The crop is the whole printed system, so say which of its bars is meant (#310).
      const barOf = row.bar_in_system ? `${row.bar_in_system}/${row.bars_in_system}` : "";
      const card = el("div", { className: "issue problem", "data-row": row.id },
        el("div", { className: "top" },
          el("span", {}, row.measure ? el("span", { className: "m" }, `m${row.measure}`) : "",
            row.measure ? "  " : "", row.part || (row.measure ? "" : "Note")),
          row.system != null ? el("span", { className: "hint" }, `system ${row.system}${barOf ? ` · bar ${barOf}` : ""}`) : ""));
      for (const n of row.notes) {
        const line = el("div", { className: "detail" },
          el("span", { className: "kind" }, NOTE_KIND[n.kind] || n.kind), " ", n.text);
        if (n.dismiss) line.append(" ", el("button", { className: "dismiss", onclick: async () => {
          await postJSON(`${P}/issues/${n.dismiss}/dismiss`); remember(row.id); refresh(); } }, "Dismiss"));
        card.append(line);
      }
      const undecided = row.choices.filter((c) => !c.decision);
      if (undecided.length && row.system != null && song.has_pdf) {
        const where = [barOf && `Bar ${row.bar_in_system} of ${row.bars_in_system}`, staffPlace(row)]
          .filter(Boolean).join(" · ");
        if (where) card.append(el("p", { className: "sub barpos" },
          row.part && row.staff_in_system ? `${row.part}: ${where}` : where));
        card.append(cropWithMark(row));
      }
      for (const c of undecided) card.append(choiceBlock(row, c));
      box.append(card);
    }
    if (decided.length) {
      box.append(el("div", { className: "issue readdone" },
        el("div", { className: "top" }, el("span", {}, el("span", { className: "kind" }, `${decided.length} decided`))),
        ...decided.map((t) => el("div", { className: "detail" }, t))));
    }
    box.append(problem);
    restore();
  }).catch((e) => {
    if (!box.isConnected) return;
    fixPlace = null;
    box.replaceChildren(el("p", { className: "lyerr readerr" }, `Could not list the problems: ${e.message}`));
    loaded();
  });
}

// Taking the score away and bringing it back. "Open in MuseScore" only opens it on
// the machine the app runs on, so from a phone — or from any other computer — the
// Fix stage could be read and never acted on. Downloading the file and sending the
// fixed one back is the same loop for everybody else.
let lastScoreUpload = null;   // {slug, text} — see below

function scoreFileTransfer(song, P, refresh) {
  const box = el("div", { className: "scorefile" });
  if (!song.has_cleaned) return box;
  // A successful upload refreshes the panel, which throws away whatever this span
  // was saying — so what it said survives the redraw. Otherwise the only sign the
  // file arrived is the health rows quietly changing.
  const status = el("span", { className: "hint scorefilestatus" },
    lastScoreUpload?.slug === song.slug ? lastScoreUpload.text : "");
  const problem = el("p", { className: "lyerr scorefileerr" });
  const picker = el("input", { type: "file", accept: ".mscx,.mscz", className: "scorefilepick",
    hidden: true });
  const upload = el("button", { className: "scorefileup" }, "Upload fixed score");

  picker.addEventListener("change", async () => {
    const file = picker.files && picker.files[0];
    if (!file) return;
    problem.textContent = "";
    // The one route that overwrites the file every later stage is derived from, so
    // it is worth one question before it does.
    if (!confirm(`Replace the cleaned score with ${file.name}?`)) { picker.value = ""; return; }
    status.textContent = `Uploading ${file.name}…`;
    upload.disabled = true;
    try {
      Object.assign(song, await api(`${P}/score-file`, { method: "POST", body: sendFile(file) }));
      lastScoreUpload = { slug: song.slug, text: `Replaced with ${file.name} — re-checked.` };
      status.textContent = lastScoreUpload.text;
      refresh();
    } catch (error) {
      lastScoreUpload = null;
      status.textContent = "";
      problem.textContent = error.message;
    }
    upload.disabled = false;
    picker.value = "";
  });
  upload.onclick = () => picker.click();

  box.append(
    el("h3", {}, "Fix it elsewhere"),
    el("p", { className: "sub" },
      "MuseScore on another machine? Download the score, fix it there, and send it back — it is re-checked on arrival, the same as an edit saved on this host."),
    el("div", { className: "row" },
      el("a", { className: "button scorefiledl", href: `${P}/score-file`, download: "" },
        "Download score (.mscx)"),
      upload, status),
    picker, problem);
  return box;
}

const sendFile = (file) => { const body = new FormData(); body.append("file", file); return body; };

// Recording a missing slur. The judgement is a person's — nothing upstream will guess
// a slur back, because it joins different pitches and cannot be pitch-checked — so all
// this does is put that judgement into fixes.json, where a re-clean replays it. The
// alternative people reach for is an empty syllable in the lyric editor, which patches
// the words and leaves the score saying two attacks where the page says one.
function slurRecorder(panel, song, P, refresh) {
  if (!song.has_cleaned) return;
  const box = el("div", { className: "slurfix" });
  panel.append(el("h3", {}, "Missing slur"),
    el("p", { className: "sub" },
      "The scan drops slurs and nothing puts them back on its own. Pick the two notes and say what the page shows — it is written into fixes.json and re-applied every clean."),
    box);

  const recorded = song.recorded_slurs || [];
  if (recorded.length) {
    box.append(el("div", { className: "issue" },
      el("div", { className: "top" }, el("span", {}, el("span", { className: "kind" }, `${recorded.length} slur(s) recorded`))),
      ...recorded.map((f) => el("div", { className: "detail" },
        `staff ${f.staff}, bar ${f.measure}, note ${(+f.index || 0) + 1} → ${(+f.index || 0) + 1 + (+f.span || 1)}: ${f.why || "no reason recorded"}`))));
  }

  const part = el("select", { className: "slurpart" });
  const measure = el("input", { type: "number", min: 1, value: 1, className: "slurbar" });
  const notes = el("div", { className: "slurnotes" }, el("span", { className: "hint" }, "Loading the score…"));
  const from = el("select", { className: "slurfrom" });
  const to = el("select", { className: "slurto" });
  const why = el("textarea", { rows: 2, className: "slurwhy",
    placeholder: "What the page shows, e.g. \"Page 1 system 2, bar 8: the tenor slurs E♭ to D over one syllable.\"" });
  const effect = el("p", { className: "hint slureffect" });
  const save = el("button", { className: "primary slursave", disabled: true }, "Record slur");
  const crop = el("div", { className: "slurcrop" });
  // The Fix stage has no log box, so a refusal said only to the log would be said to
  // nobody — and a refused fix is exactly what the person has to see.
  const problem = el("p", { className: "lyerr slurerr" });

  box.append(
    el("div", { className: "row" }, el("label", {}, "Part"), part, el("label", {}, "Bar"), measure),
    crop, notes,
    el("div", { className: "row" }, el("label", {}, "Slur from"), from, el("label", {}, "to"), to),
    effect,
    el("label", {}, "Why — what does the page show?"), why,
    el("div", { className: "row" }, save), problem);

  let bar = null;
  let systems = null;   // measure ranges of the printed systems, or [] when unavailable

  const loadSystems = async () => {
    if (systems !== null) return systems;
    // The compare view renders the cleaned score, which needs MuseScore and takes
    // seconds. Not having it costs a picture, not the feature.
    try { systems = (await getJSON(`${P}/compare`)).systems || []; }
    catch { systems = []; }
    return systems;
  };

  const showCrop = async (m) => {
    const found = (await loadSystems()).find((s) => m >= s.measure_start && m <= s.measure_end);
    crop.replaceChildren(...(found ? [el("img", { className: "slurcropimg", loading: "lazy",
      src: `${P}/cleaned-system/${found.index}?dpi=300`, alt: `cleaned system ${found.index + 1}` })] : []));
  };

  const describe = () => {
    if (!bar || !bar.notes?.length) return;
    const a = +from.value, b = +to.value;
    const ok = Number.isFinite(a) && Number.isFinite(b) && b > a;
    save.disabled = !ok;
    if (!ok) { effect.textContent = "The slur has to reach a later note."; return; }
    // Every note it reaches stops taking a syllable, so the bar loses that many.
    const losing = bar.notes.slice(a + 1, b + 1).filter((n) => n.carries_syllable).length;
    effect.textContent =
      `${bar.notes[a].name} → ${bar.notes[b].name}, held over one syllable. `
      + `This bar goes from ${bar.syllables} syllable(s) to ${bar.syllables - losing}.`;
  };

  // Which bar is on screen. Typing a bar number and then clicking Record fires
  // `change` a second time as the field loses focus, and reloading throws away the
  // picked notes and disables the button under the click that was already happening.
  let shown = null;

  const loadBar = async () => {
    const asked = `${part.value}:${measure.value}`;
    if (asked === shown) return;
    shown = asked;
    notes.replaceChildren(el("span", { className: "hint" }, "Reading the bar…"));
    from.replaceChildren(); to.replaceChildren(); save.disabled = true;
    let data;
    try { data = await getJSON(`${P}/bar?staff=${+part.value}&measure=${+measure.value}`); }
    catch (e) {
      bar = null;
      shown = null;               // so asking for the same bar again really asks
      notes.replaceChildren(el("span", { className: "hint" }, String(e.message || e)));
      effect.textContent = "";
      return;
    }
    bar = data;
    if (!data.notes.length) {
      notes.replaceChildren(el("span", { className: "hint" }, "No notes in that bar — only rests."));
      effect.textContent = "";
      return;
    }
    notes.replaceChildren(...data.notes.map((n, i) => el("span", {
      className: "slurnote" + (n.carries_syllable ? "" : " nosyl") + (n.starts_slur ? " slurred" : ""),
      title: n.starts_slur ? "already slurred" : (n.carries_syllable ? "takes a syllable" : "no syllable — under a slur or tie"),
    }, `${i + 1}. ${n.name}`)));
    for (const [sel, list] of [[from, data.notes.slice(0, -1)], [to, data.notes.slice(1)]]) {
      sel.replaceChildren(...list.map((n) => {
        const i = data.notes.indexOf(n);
        return el("option", { value: String(i), disabled: sel === from && n.starts_slur },
          `${i + 1}. ${n.name}${sel === from && n.starts_slur ? " (already slurred)" : ""}`);
      }));
    }
    // Start on the first note that can still open a slur; every note but the last can.
    const first = data.notes.findIndex((n, i) => i < data.notes.length - 1 && !n.starts_slur);
    from.value = String(first < 0 ? 0 : first);
    to.value = String(Math.min(+from.value + 1, data.notes.length - 1));
    describe();
    showCrop(+measure.value);
  };

  part.onchange = loadBar;
  measure.onchange = loadBar;
  from.onchange = to.onchange = describe;

  save.onclick = async () => {
    if (song.lyrics?.json && !confirm(
      "This takes a syllable out of that bar. Lyrics are already imported, so that "
      + "line will come back one syllable too long and needs re-entering. Record it anyway?")) return;
    save.disabled = true;
    problem.textContent = "";
    try {
      const fresh = await postJSON(`${P}/fixes/slur`, {
        staff: +part.value, measure: +measure.value,
        index: +from.value, span: +to.value - +from.value, why: why.value,
      });
      Object.assign(song, fresh);
      appendLog("Slur recorded in fixes.json and applied to the cleaned score.");
      refresh();
    } catch (e) {
      problem.textContent = `Could not record it: ${e.message || e}`;
      save.disabled = false;
    }
  };

  getJSON(`${P}/bar`).then((data) => {
    if (!data.parts.length) {
      notes.replaceChildren(el("span", { className: "hint" }, "No singing parts in the cleaned score."));
      return;
    }
    part.replaceChildren(...data.parts.map((p) =>
      el("option", { value: String(p.staff) }, p.name)));
    measure.max = data.measures || 1;
    return loadBar();
  }).catch((e) => notes.replaceChildren(el("span", { className: "hint" }, String(e.message || e))));
}

async function panelLyrics(panel, song, P, refresh) {
  const mode = localStorage.getItem("lyricMode") === "manual" ? "manual" : "paste";
  const swap = (m) => {
    localStorage.setItem("lyricMode", m);
    panel._refreshInPlace = null;
    panel.replaceChildren();
    panelLyrics(panel, song, P, refresh);
  };
  panel.append(
    el("h2", {}, "Lyrics"),
    el("div", { className: "row" },
      el("button", { className: mode === "paste" ? "vtab active" : "vtab", onclick: () => swap("paste") }, "Paste from AI"),
      el("button", { className: mode === "manual" ? "vtab active" : "vtab", onclick: () => swap("manual") }, "Type by system")));
  if (mode === "manual") return lyricsManual(panel, song, P, refresh);
  return lyricsPaste(panel, song, P, refresh);
}

// Import mismatches come from the server as fields (kind, measure range, staff ids,
// syllables, slots, message). Songs imported before that shipped hold the sentence as
// a plain string; read the fields back out of it so those keep their cell markers too.
const LEGACY_WARN = /^Measures (\d+)-(\d+) \(staffs ([\d,\s]+)\): (too many|too few) tokens \((\d+) syllables, (\d+) slots\)/;

function lyricMismatches(song) {
  return (song.lyrics?.warnings || []).map((w) => {
    if (typeof w !== "string") return w;
    const m = w.match(LEGACY_WARN);
    if (!m) return { message: w, staff_ids: [] };
    return {
      kind: m[4] === "too many" ? "too_many" : "too_few", message: w,
      measure_start: +m[1], measure_end: +m[2],
      staff_ids: m[3].split(",").map((s) => +s.trim()).filter(Number.isFinite),
      syllables: +m[5], slots: +m[6],
    };
  });
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

// An import brings the panel up to date where it stands rather than redrawing it,
// so the place in the list and the box being typed in survive (#336). A refresh
// that arrives while the import is still running is ignored -- the import's own
// answer is the one to show -- and one about a score that changed some other way
// (a re-clean) is refused, so the panel is drawn afresh from the new score.
function lyricsInPlace(panel, song, update) {
  const live = { fp: song.cleaned_fingerprint, busy: false };
  panel._refreshInPlace = () => {
    if (live.busy) return true;
    if (song.cleaned_fingerprint !== live.fp) return false;
    update();
    return true;
  };
  return live;
}

async function lyricsPaste(panel, song, P, refresh) {
  const warnBox = el("div", {});
  const drawWarns = () => {
    const warns = lyricMismatches(song);
    warnBox.replaceChildren(...(warns.length ? [
      el("p", { className: "sub" }, "Mismatches (often a note problem — check the measure in MuseScore):"),
      el("ul", { className: "warnlist" }, warns.map((w) => el("li", {}, w.message)))] : []));
  };
  drawWarns();
  panel.append(warnBox);
  const live = lyricsInPlace(panel, song, drawWarns);
  panel.append(el("p", { className: "sub" }, "No API key needed — your AI does the reading, this catches the result."));
  const ta = el("textarea", { rows: 12, placeholder: "Paste the lyric JSON from your AI chat here…" });
  if (song.lyrics?.json) {
    try { ta.value = await api(`/api/songs/${encodeURIComponent(song.slug)}/lyrics-json`); } catch {}
  }
  const aiLinks = [
    ["Claude", "https://claude.ai/new"],
    ["ChatGPT", "https://chatgpt.com/"],
    ["Gemini", "https://gemini.google.com/app"],
  ].map(([name, url]) => el("a", { className: "ailink", href: url, target: "_blank", rel: "noopener" }, name));
  panel.append(
    el("div", { className: "row" },
      el("button", { onclick: async () => {
        const { prompt } = await getJSON("/api/prompt");
        await navigator.clipboard.writeText(prompt);
        appendLog("Prompt copied — paste it into your AI chat with the PDF.");
      }}, "1. Copy prompt"),
      el("button", { onclick: () => postJSON(`${P}/reveal-pdf`) }, "Reveal PDF in Finder")),
    el("div", { className: "row" }, el("span", { className: "hint" }, "Open your AI:"), ...aiLinks),
    el("label", {}, "2. Paste the returned JSON"), ta,
    el("div", { className: "row" },
      el("button", { className: "primary", onclick: async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        live.busy = lyricImporting = true;
        appendLog("Importing lyrics…");
        try {
          const fresh = await postJSON(`${P}/lyrics`, { json: ta.value });
          Object.assign(song, fresh);
          live.fp = song.cleaned_fingerprint;
          drawWarns();
          const w = song.lyrics?.warnings || [];
          if (w.length) appendLog(`Imported with ${w.length} warning(s).`);
          else appendLog("Imported cleanly. Ready for review.");
          // Pasted JSON can touch any system, so every one is fetched again.
          cleanedSystemsChanged(song.slug, song.cleaned_fingerprint, "all");
        } catch (e) { appendLog(e.message, true); }
        live.busy = lyricImporting = false;
        btn.disabled = false;
        refresh();
      }}, "3. Import lyrics")));
  panel.append(makeLog());
}

async function lyricsManual(panel, song, P, refresh) {
  panel.append(el("p", { className: "sub" }, "Type the lyrics for each part, per system. Hyphenate syllables (e.g. \"lau-lun ai-ka\")."));
  const toggle = el("button", { className: "vtab" }, "");
  toggle.onclick = () => {
    showScore = !showScore;
    localStorage.setItem("lyricScore", showScore ? "1" : "0");
    lyricScroll = panel.scrollTop;
    panel._refreshInPlace = null; // this one does want the panel drawn again
    refresh();
  };
  panel.append(toggle);
  const holder = el("div", {}, el("p", { className: "hint" }, "Loading…"));
  panel.append(holder, makeLog());

  let grid;
  try { grid = await getJSON(`${P}/lyric-grid`); }
  catch (e) { holder.replaceChildren(el("p", { className: "err" }, e.message)); return; }

  // The printed system, cropped from the scan, shown beside the cells for it.
  // Typing lyrics against a whole page is the resolution problem all over again:
  // a slur or a lyric line under the lower staff is not legible at page scale.
  // Matched by measure range, not by position, so a bounds file that disagrees
  // with the score attaches nothing rather than the wrong picture.
  let byStart = {};
  try {
    const b = await getJSON(`${P}/bounds`);
    for (const s of b.systems || []) if (s.measure_start) byStart[s.measure_start] = s.index;
  } catch { /* no PDF, or none stored yet — the cells work regardless */ }
  // Off by default now that focusing a cell shows the system in the viewer.
  let showScore = localStorage.getItem("lyricScore") === "1";
  const { parts, systems, cells, capacities } = grid;
  const cellText = (si, name) => (cells?.[si]?.[name]) || "";
  // Attach a mismatch to the system where its line STARTS (a line can span several
  // systems if the part is blank in later ones); show the full measure range.
  const cellWarns = (sys, p) => lyricMismatches(song).filter((w) =>
    (w.staff_ids || []).includes(p.id) && w.measure_start >= sys.start && w.measure_start <= sys.end);
  const warnLines = (sys, p) => cellWarns(sys, p).map((w) => el("div", { className: "lyerr" },
    `⚠ m${w.measure_start}–${w.measure_end}`
    + `${w.measure_end > sys.end ? " (spans later systems)" : ""}: `
    + `${w.message.split("): ").pop()}`));
  const warnBoxes = [];
  let typingIn = null; // the box last typed in, given back its focus after an import

  const scoreFor = (sys) => {
    const idx = byStart[sys.start];
    if (!idx || !showScore) return [];
    return [el("a", { className: "syspeek", href: `${P}/system/${idx}?dpi=400`,
                      target: "_blank", title: "open at full resolution" },
      el("img", { src: `${P}/system/${idx}?dpi=200`, loading: "lazy",
                  alt: `system ${idx}` }))];
  };

  holder.replaceChildren(...systems.map((sys) =>
    el("div", { className: "sysblock" },
      el("h4", {}, `System ${sys.index + 1} — measures ${sys.start}–${sys.end}`),
      ...scoreFor(sys),
      ...parts.map((p) => {
        const ta = el("textarea", { rows: 1, value: cellText(sys.index, p.name),
          "data-sys": sys.index, "data-part": p.name });
        ta.oninput = () => autoGrow(ta);
        const shown = byStart[sys.start];
        ta.onfocus = () => { typingIn = ta; if (shown) showSystem(shown); };
        const errs = el("div", { className: "lyerrs" }, ...warnLines(sys, p));
        warnBoxes.push([errs, sys, p]);
        return el("div", { className: "lyrow" },
          el("label", {}, `${p.name} · ${capacities?.[sys.index]?.[p.name] ?? 0} lyric slots`), ta, errs);
      }))));
  toggle.textContent = Object.keys(byStart).length
    ? (showScore ? "Hide the score" : "Show the score")
    : "";
  toggle.style.display = Object.keys(byStart).length ? "" : "none";
  holder.querySelectorAll("textarea").forEach(autoGrow); // size to content (no scroll)
  if (lyricScroll != null) { panel.scrollTop = lyricScroll; lyricScroll = null; } // restore after "Show the score"

  // The warnings are the only thing an import changes in this panel, so they are
  // the only thing redrawn: the boxes, the one being typed in and the scroll stay.
  const drawWarns = () => {
    for (const [errs, sys, p] of warnBoxes) errs.replaceChildren(...warnLines(sys, p));
  };
  const live = lyricsInPlace(panel, song, drawWarns);
  const readCells = () => {
    const map = {};
    panel.querySelectorAll("textarea[data-sys]").forEach((t) => {
      (map[t.dataset.sys] ||= {})[t.dataset.part] = t.value.trim();
    });
    return map;
  };
  let imported = readCells(); // what the score holds now, to tell what an import changes

  // After an import each box shows what actually landed -- the words, and a `_` for
  // every slot left without one -- written into the boxes already there. The box
  // being typed in keeps its caret, and whatever is in view stays where it is on
  // screen even when a box above it grows.
  const showLanded = async () => {
    let fresh;
    try { fresh = await getJSON(`${P}/lyric-grid`); } catch { return; }
    const boxes = [...panel.querySelectorAll("textarea[data-sys]")];
    const anchor = (typingIn?.isConnected && typingIn)
      || boxes.find((t) => t.getBoundingClientRect().bottom > panel.getBoundingClientRect().top);
    const y = anchor?.getBoundingClientRect().top;
    for (const t of boxes) {
      const text = fresh.cells?.[t.dataset.sys]?.[t.dataset.part] || "";
      if (t.value === text) continue;
      const caret = t === typingIn ? t.selectionStart : null;
      t.value = text;
      autoGrow(t);
      if (caret != null) t.setSelectionRange(caret, caret);
    }
    if (anchor) panel.scrollTop += anchor.getBoundingClientRect().top - y;
    imported = readCells();
  };

  // The printed systems an import touches: each one where a part's text differs
  // from what was imported last, and the systems after it where that part is left
  // blank, because a blank box carries the line on and the words spill into them.
  const changedSystems = (now) => {
    const touched = new Set();
    for (const p of parts) {
      let carrying = false;
      for (const sys of systems) {
        const text = now[sys.index]?.[p.name] || "";
        const before = imported[sys.index]?.[p.name] || "";
        if (text !== before) { touched.add(sys); carrying = true; }
        else if (text) carrying = false;
        else if (carrying) touched.add(sys);
      }
    }
    const printed = [...touched].map((sys) => byStart[sys.start]);
    return printed.every(Boolean) ? printed.sort((a, b) => a - b) : "all";
  };

  const doImport = async (btn) => {
    const map = readCells();
    if (!Object.values(map).some((row) => Object.values(row).some((t) => t))) {
      appendLog("Nothing typed yet.", true); return;
    }
    btn.disabled = true;
    live.busy = lyricImporting = true;
    appendLog("Importing lyrics…");
    try {
      const fresh = await postJSON(`${P}/lyrics`, { cells: map });
      Object.assign(song, fresh);
      live.fp = song.cleaned_fingerprint;
      drawWarns();
      const w = song.lyrics?.warnings || [];
      appendLog(w.length ? `Imported with ${w.length} warning(s).` : "Imported cleanly. Ready for review.");
      cleanedSystemsChanged(song.slug, song.cleaned_fingerprint, changedSystems(map));
      imported = map;
      await showLanded();
    } catch (e) { appendLog(e.message, true); }
    live.busy = lyricImporting = false;
    btn.disabled = false;
    // Pressing the button took the focus (and disabling it dropped it on the page);
    // give it back to the box being typed in, without scrolling to it.
    const away = document.activeElement;
    if (typingIn?.isConnected && (away === btn || away === document.body || !away))
      typingIn.focus({ preventScroll: true });
    refresh(); // the stage rail and the viewer; this panel only updates its warnings
  };
  const importBtn = el("button", { className: "primary", onclick: () => doImport(importBtn) }, "Import lyrics");
  panel.append(el("div", { className: "floatbar" }, importBtn));
}

function panelReview(panel, song, P, refresh, actions) {
  const approve = el("button", { className: "primary review-approve", onclick: async () => {
    approve.disabled = true;
    try {
      Object.assign(song, await postJSON(`${P}/approve-review`, {
        cleaned_fingerprint: song.verification_summary?.cleaned_fingerprint,
      }));
      actions.selectStage("record");
    } catch (error) {
      approve.disabled = false;
      alert(error.message);
    }
  } }, "✓ Approve → Record");
  panel.append(el("section", { className: "review-panel" },
    el("h2", {}, "Review"),
    el("p", { className: "sub" }, "Final check before recording."),
    compactReview(song.verification_summary),
    el("div", { className: "review-full" }, verificationView(song.verification_summary)),
    el("div", { className: "review-secondary row" },
      el("button", { onclick: async () => {
        Object.assign(song, await postJSON(`${P}/rescan`));
        refresh();
      } }, "Re-run health check"),
      el("button", { onclick: () => postJSON(`${P}/open-score`) }, "Open in MuseScore")),
    scoreFileTransfer(song, P, refresh),
    el("div", { className: "review-actions" }, approve)));
}

function panelRecord(panel, song, P, refresh, actions) {
  const rec = song.record || {};
  const recording = song.recording;
  const recorded = !!(rec.outputs && rec.outputs.length);
  const summary = song.verification_summary || {};
  const parts = summary.expected_parts || [];
  const approvedAgainst = song.review?.approved_against;
  const currentScore = summary.cleaned_fingerprint;
  const approved = !!approvedAgainst && approvedAgainst === currentScore;
  const approvalStale = !!approvedAgainst && approvedAgainst !== currentScore;
  // Two ways to make the videos. The scrolling renderer draws them from the score
  // and is the default; the screen recorder drives MuseScore and needs macOS.
  let renderer = rec.renderer || "scroll";

  panel.append(el("h2", {}, "Record"));
  const sub = el("p", { className: "sub" }, "");
  panel.append(sub);

  const approval = el("div", { className: `approval-summary ${approved ? "approved" : "attention"}` },
    el("strong", {}, approved ? "✓ Score approved" : approvalStale ? "⚠ Score changed after approval" : "⚠ Review not approved"),
    el("span", {}, approved
      ? `${parts.length} part${parts.length === 1 ? "" : "s"} · ${summary.lyrics?.status === "passed" ? "Lyrics ready" : "Check lyric status"}`
      : approvalStale ? "Return to Review before rendering the changed score." : "Approve the score in Review before rendering."),
    approved ? "" : el("button", { onclick: () => actions.selectStage("review") }, "Back to Review"));
  panel.append(approval);

  if (recording) {
    panel.append(el("div", { className: "banner" }, "● Rendering… leave this running."));
  } else if (recorded) {
    panel.append(el("div", { className: "banner good" },
      "✓ Ready" + (rec.renderer === "screen" ? " (screen recording)." : ".")));
  }
  if (rec.error) {
    panel.append(el("div", { className: "banner err" }, "Last run failed: " + rec.error));
  } else if (song.jobs?.render?.status === "failed") {
    panel.append(el("div", { className: "banner err" },
      "Last render failed: " + song.jobs.render.error));
  }

  const scrollRadio = el("input", { type: "radio", name: "renderer", checked: renderer === "scroll" });
  const screenRadio = el("input", { type: "radio", name: "renderer", checked: renderer === "screen" });
  const quality = el("select", {},
    el("option", { value: "4k", selected: (rec.quality || "4k") === "4k" }, "4K, 60fps"),
    el("option", { value: "1080p", selected: rec.quality === "1080p" }, "1080p, 30fps (faster)"),
    el("option", { value: "720p", selected: rec.quality === "720p" }, "720p, 30fps (test)"));
  // What a song that has never been asked starts at. The server prefills the same
  // numbers (DEFAULT_TOP/BOTTOM_MARGIN_PERCENT); keep the two in step. A little
  // white space under the bottom staff keeps its lyrics off the frame edge.
  const topMargin = el("input", {
    type: "number", value: rec.top_margin ?? DEFAULT_TOP_MARGIN, min: -40, max: 100, step: 1,
    style: "width:100px", "data-video-margin": "top"
  });
  const bottomMargin = el("input", {
    type: "number", value: rec.bottom_margin ?? DEFAULT_BOTTOM_MARGIN, min: -40, max: 100, step: 1,
    style: "width:100px", "data-video-margin": "bottom"
  });
  // Parts that share a staff in the picture (#246): "S1+S2, A1+A2" draws four
  // parts on two staves. The videos and their mixes stay one per part.
  const staffGroups = el("input", {
    type: "text", value: rec.staff_groups ?? "", placeholder: "e.g. S1+S2, A1+A2",
    style: "width:220px", "data-staff-groups": ""
  });
  const hardwareEncoding = el("input", {
    type: "checkbox", checked: rec.hardware_encoding !== false, "data-hardware-encoding": ""
  });
  const bpm = el("input", {
    type: "number", value: rec.bpm ?? 80, min: 20, max: 300, step: 1,
    style: "width:120px"
  });
  // Tempo changes printed in the score (#387): from a bar on, play at another
  // BPM, or back at the opening one ("Tempo I"). Cleaning keeps no tempo marks,
  // so the render puts these back; they live with the Record settings.
  const tempoRows = el("div", { className: "tempo-changes", "data-tempo-changes": "" });
  const tempoChanged = () => {
    actions.previewInputsChanged();
    saveNote.className = "hint save-note";
    saveNote.textContent = "Unsaved changes";
  };
  const addTempoRow = (change = {}) => {
    const start = change.bpm === "start";
    const bar = el("input", { type: "number", min: 2, step: 1, value: change.measure ?? "",
      placeholder: "bar", style: "width:64px", "data-tempo-bar": "" });
    const speed = el("input", { type: "number", min: 20, max: 300, step: 1,
      value: start ? "" : (change.bpm ?? ""), disabled: start, placeholder: "BPM",
      style: "width:72px", "data-tempo-bpm": "" });
    const back = el("input", { type: "checkbox", checked: start, "data-tempo-start": "" });
    const row = el("div", { className: "row tempo-row" },
      el("span", {}, "Bar"), bar, speed,
      el("label", { className: "tempo-start" }, back, " back to start"),
      el("button", { title: "Remove this tempo change", onclick: () => { row.remove(); tempoChanged(); } }, "✕"));
    back.addEventListener("input", () => { speed.disabled = back.checked; });
    for (const control of [bar, speed, back]) control.addEventListener("input", tempoChanged);
    tempoRows.append(row);
    return row;
  };
  for (const change of rec.tempo_changes || []) addTempoRow(change);
  const tempoChanges = () => [...tempoRows.querySelectorAll(".tempo-row")]
    .map((row) => ({
      measure: Number(row.querySelector("[data-tempo-bar]").value),
      start: row.querySelector("[data-tempo-start]").checked,
      bpm: Number(row.querySelector("[data-tempo-bpm]").value),
    }))
    .filter((c) => c.measure)
    .map((c) => ({ measure: c.measure, bpm: c.start ? "start" : c.bpm }));
  const addTempo = el("button", { onclick: () => { addTempoRow().querySelector("input").focus(); } },
    "+ Add tempo change");
  const delay = el("input", { type: "number", value: rec.audio_delay_ms ?? 1300, step: 50, style: "width:120px" });
  const redoMp3 = el("input", { type: "checkbox" });
  const redoVideo = el("input", { type: "checkbox" });

  const post = async (extra, msg) => {
    appendLog(msg);
    try {
      await postJSON(`${P}/record`, { renderer, ...extra });
      refresh();
    } catch (e) { appendLog(e.message, true); }
  };

  const runBtn = el("button", { className: "primary", disabled: recording || !approved, onclick: () =>
    renderer === "scroll"
      ? post({ quality: quality.value, hardware_encoding: hardwareEncoding.checked,
               top_margin: Number(topMargin.value) || 0,
               bottom_margin: Number(bottomMargin.value) || 0,
               staff_groups: staffGroups.value.trim(),
               tempo_changes: tempoChanges(),
               ...(song.needs_initial_bpm ? { bpm: Number(bpm.value) } : {}) },
             "Rendering the scrolling video…")
      : post({ audio_delay_ms: Number(delay.value) || 1300,
               redo_mp3: redoMp3.checked, redo_video: redoVideo.checked }, "Starting…") }, "");
  const remergeBtn = el("button", { disabled: recording, onclick: () =>
    post({ merge_only: true, audio_delay_ms: Number(delay.value) || 1300 },
         "Re-merging with new offset…") }, "Re-merge only (apply offset)");

  const scrollCommon = el("div", { className: "record-common" },
    el("label", {}, "Output"),
    el("div", { className: "row output-row" }, quality,
      el("span", { className: "hint" }, "4K keeps panning smooth and text sharp")),
    ...(song.needs_initial_bpm ? [
      el("label", {}, "Tempo (BPM)"),
      el("div", { className: "row" }, bpm,
        el("span", { className: "hint" }, "this score has no opening tempo marking"))
    ] : []),
    el("label", {}, "Tempo changes"),
    tempoRows,
    el("div", { className: "row" }, addTempo,
      el("span", { className: "hint" }, "where the page changes speed; bars counted from 1")));
  const scrollAdvanced = el("div", {},
    el("label", {}, "Vertical margins"),
    el("div", { className: "row" },
      el("span", {}, "Top margin"), topMargin, el("span", {}, "%")),
    el("div", { className: "row" },
      el("span", {}, "Bottom margin"), bottomMargin, el("span", {}, "%")),
    el("p", { className: "hint" }, "0 = current layout; positive adds white space; negative crops that edge"),
    el("label", {}, "Shared staves"),
    el("div", { className: "row" }, staffGroups),
    el("p", { className: "hint" },
      "Two parts on one staff, upper part first; blank = one staff per part. Still one video per part."
      + (parts.length ? ` Parts: ${parts.join(" ")}` : "")),
    el("div", { className: "row" }, hardwareEncoding,
      el("span", {}, "Use NVIDIA hardware encoding when available")));
  const screenAdvanced = el("div", {},
    el("label", {}, "Audio sync offset (ms)"),
    el("div", { className: "row" }, delay,
      el("span", { className: "hint" }, "shift audio vs. video; re-merge to apply")),
    el("div", { className: "row" }, redoMp3, el("span", {}, "Re-export MP3")),
    el("div", { className: "row" }, redoVideo, el("span", {}, "Re-record video")),
    el("div", { className: "row" }, remergeBtn));

  const scrollCard = el("label", { className: "renderer-card" }, scrollRadio,
    el("span", {}, el("strong", {}, "Scrolling score"),
      el("small", {}, "Unattended · recommended")));
  const screenCard = el("label", { className: "renderer-card" }, screenRadio,
    el("span", {}, el("strong", {}, "Screen recording"),
      el("small", {}, "MuseScore + QuickRecorder · macOS")));
  const advanced = el("details", { className: "record-advanced" },
    el("summary", {}, "Framing & advanced settings"),
    scrollAdvanced, screenAdvanced);
  const previewSettings = () => ({
    quality: quality.value,
    top_margin: Number(topMargin.value) || 0,
    bottom_margin: Number(bottomMargin.value) || 0,
    staff_groups: staffGroups.value.trim(),
    tempo_changes: JSON.stringify(tempoChanges()),
    ...(song.needs_initial_bpm ? { bpm: Number(bpm.value) } : {}),
  });
  // Saved on Preview and on Save settings (#301), not only once a preview or a
  // render has come out: opening the preview is when the choice is made, and a
  // preview nobody pressed "Preview scroll" for used to keep nothing.
  const saveNote = el("span", { className: "hint save-note", "data-save-note": "" }, "");
  const saveSettings = async () => {
    try {
      const saved = await postJSON(`${P}/record-settings`,
        { ...previewSettings(), hardware_encoding: hardwareEncoding.checked });
      Object.assign(rec, saved.record || {});
      song.record = rec;
      saveNote.className = "hint save-note";
      saveNote.textContent = "Saved";
      return true;
    } catch (e) {
      // Said beside the button that was pressed: the log is below the fold.
      saveNote.className = "hint save-note err";
      saveNote.textContent = e.message;
      return false;
    }
  };
  const saveBtn = el("button", { onclick: saveSettings }, "Save settings");
  const previewBtn = el("button", { className: "preview-action", onclick: async () => {
    if (await saveSettings()) actions.openPreview({ start: true });
  } }, "Preview");

  actions.setPreviewSettings(previewSettings);
  for (const control of [quality, topMargin, bottomMargin, bpm, staffGroups]) {
    control.addEventListener("input", actions.previewInputsChanged);
  }
  for (const control of [quality, topMargin, bottomMargin, bpm, staffGroups, hardwareEncoding]) {
    control.addEventListener("input", () => {
      saveNote.className = "hint save-note";
      saveNote.textContent = "Unsaved changes";
    });
  }

  const applyRenderer = () => {
    scrollRadio.checked = renderer === "scroll";
    screenRadio.checked = renderer === "screen";
    scrollCard.classList.toggle("selected", renderer === "scroll");
    screenCard.classList.toggle("selected", renderer === "screen");
    scrollCommon.style.display = renderer === "scroll" ? "" : "none";
    scrollAdvanced.style.display = renderer === "scroll" ? "" : "none";
    screenAdvanced.style.display = renderer === "screen" ? "" : "none";
    previewBtn.style.display = renderer === "scroll" ? "" : "none";
    saveBtn.style.display = renderer === "scroll" ? "" : "none";
    saveNote.style.display = renderer === "scroll" ? "" : "none";
    if (renderer === "screen") actions.pausePreview();
    previewBtn.parentElement?.classList.toggle("screen", renderer === "screen");
    const count = parts.length ? ` all ${parts.length} parts` : " videos";
    runBtn.textContent = renderer === "scroll" ? `Render${count}` : `Record${count}`;
    sub.textContent = renderer === "scroll"
      ? "Draw the scrolling score straight from the notes — nothing on screen, runs unattended."
      : "Export per-voice audio, record + merge the play-along video. macOS only.";
  };
  const choose = (which) => { renderer = which; applyRenderer(); };

  panel.append(
    el("label", {}, "Video style"),
    el("div", { className: "renderer-choices" }, scrollCard, screenCard),
    scrollCommon,
    advanced,
    el("div", { className: "record-actions" }, saveBtn, previewBtn, runBtn),
    saveNote,
    makeLog(song.jobs?.render));

  scrollRadio.onclick = () => choose("scroll");
  screenRadio.onclick = () => choose("screen");
  applyRenderer();

  // --- results review ---
  const merged = (song.media || []).filter((m) => m.merged);
  if (song.media && song.media.length) {
    panel.append(
      el("h3", {}, "Results"),
      el("div", { className: "row" },
        el("button", { onclick: () => postJSON(`${P}/reveal-media`) }, "Reveal in Finder"),
        recorded ? el("button", { onclick: async () => { await postJSON(`${P}/stage/upload`); refresh(); } }, "→ Upload to YouTube") : ""),
      ...(merged.length ? merged : song.media).map((m) =>
        el("div", { className: "result" },
          el("div", { className: "rlabel" }, m.label),
          el("video", { src: m.url, controls: true, preload: "metadata" }))));
  }
}

function panelUpload(panel, song, P, refresh) {
  panel.append(el("h2", {}, "Upload"),
    el("p", { className: "sub" }, "Upload the recorded videos to YouTube and (optionally) add them to a playlist."),
    verificationView(song.verification_summary));

  const rec = song.record || {};
  const recording = song.recording;
  const recorded = !!(rec.outputs && rec.outputs.length);
  const uploads = rec.uploads || [];
  const ups = song.upload_status || { videos: [], local_count: 0 };
  const onDisk = ups.local_count > 0;

  if (recording) {
    panel.append(el("div", { className: "banner" }, "● Working… leave this running."));
  } else if (ups.complete) {
    panel.append(el("div", { className: "banner good" }, "✓ Uploaded to YouTube."));
  } else if (uploads.length) {
    panel.append(el("div", { className: "banner" }, "Only some of the videos are on YouTube."));
  }
  if (rec.error) {
    panel.append(el("div", { className: "banner err" }, "Last run failed: " + rec.error));
  } else if (song.jobs?.upload?.status === "failed") {
    panel.append(el("div", { className: "banner err" },
      "Last upload failed: " + song.jobs.upload.error));
  }
  if (!recorded) {
    panel.append(el("p", { className: "hint" }, "Nothing to upload yet — record the videos first."));
  } else if (!onDisk) {
    panel.append(el("p", { className: "hint" }, rec.freed
      ? "The local videos were freed after the upload. Record again to make them."
      : "The videos are not on disk. Record again to make them."));
  }

  // playlist picker
  const pl = el("input", { placeholder: "YouTube playlist id (optional)" });
  const plPick = el("select", {}, el("option", { value: "" }, "— previous playlists —"));
  let plTitle = "";
  getJSON("/api/playlists").then((lists) => {
    for (const l of lists) plPick.append(el("option", { value: l.id }, l.title || l.id));
    plPick.onchange = () => {
      if (!plPick.value) return;
      pl.value = plPick.value;
      plTitle = plPick.options[plPick.selectedIndex].textContent;
    };
  }).catch(() => {});

  const uploadBtn = el("button", { className: "primary", disabled: recording || !recorded || !onDisk,
    onclick: async () => {
      appendLog("Uploading to YouTube…");
      try {
        await postJSON(`${P}/record`, {
          upload_only: true, playlist: pl.value.trim() || null, playlist_title: plTitle || null,
        });
        refresh();
      } catch (e) { appendLog(e.message, true); }
    } }, uploads.length ? "Re-upload to YouTube" : "Upload to YouTube");

  panel.append(
    el("label", {}, "Playlist"),
    el("div", { className: "row" }, plPick),
    pl,
    el("div", { className: "row" }, uploadBtn),
    makeLog(latestJob(song.jobs, ["upload", "render", "publish"])));

  if (uploads.length) {
    panel.append(
      el("h3", {}, "Uploaded videos"),
      el("ul", { className: "uploads" }, uploads.map((u) =>
        el("li", {}, el("a", { href: u.url, target: "_blank", rel: "noopener" }, u.title || u.url)))),
      freeSpaceSection(P, song, ups, recording, refresh),
      el("div", { className: "row" },
        el("button", { disabled: recording, onclick: async () => {
          if (!confirm("Delete these videos from YouTube? You can then re-upload.")) return;
          appendLog("Deleting from YouTube…");
          try { await postJSON(`${P}/youtube-delete`); appendLog("Deleted."); refresh(); }
          catch (e) { appendLog(e.message, true); }
        }}, "Delete from YouTube")),
      playlistSection(P, recording));
  }
  panel.append(publishSection(P, song, refresh));
}

// The new Cloudflare site (#382): the score, one MP3 per part and the timing,
// listed for one choir. Made off the approved score, not off the videos, so it
// needs no render; YouTube above keeps working until the new site replaces it.
const CHOIRS = { jm: "Joensuun Mieslaulajat (jm)", naiskuoro: "Naiskuoro", public: "Public demo" };

function publishSection(P, song, refresh) {
  const pub = song.publish || {};
  const site = pub.site;
  const job = song.jobs?.publish;
  const running = job?.status === "running";
  const guess = pub.choir || { men: "jm", women: "naiskuoro" }[song.voicing] || "";
  const choir = el("select", { className: "publish-choir" },
    el("option", { value: "" }, "— choir —"),
    ...Object.entries(CHOIRS).map(([value, label]) => el("option", { value }, label)));
  choir.value = guess;
  const status = el("p", { className: "hint publish-status" });
  if (!song.publish_configured) {
    status.textContent = "Publishing is off on this host: the Cloudflare keys are not in .env.";
  } else if (running) {
    status.textContent = "Publishing… the log above shows how far it is.";
  } else if (pub.error) {
    status.className = "hint publish-status err";
    status.textContent = "Last publish failed: " + pub.error;
  } else if (site) {
    const stale = pub.published_against && song.cleaned_fingerprint
      && pub.published_against !== song.cleaned_fingerprint;
    status.textContent = `Published to ${CHOIRS[site.choir] || site.choir} on `
      + `${new Date(site.at * 1000).toLocaleString()} — ${site.parts.length} part(s).`
      + (stale ? " The score has changed since; publish again to update it." : "");
  } else {
    status.textContent = "Not published yet.";
  }
  const btn = el("button", { className: "primary publish-btn",
    disabled: !song.publish_configured || running || song.recording,
    onclick: async () => {
      if (!choir.value) { status.className = "hint publish-status err"; status.textContent = "Choose a choir first."; return; }
      btn.disabled = true;
      appendLog("Publishing to the new site…");
      try { await postJSON(`${P}/publish`, { choir: choir.value }); refresh(); }
      catch (e) {
        status.className = "hint publish-status err";
        status.textContent = e.message;
        btn.disabled = false;
      }
    } }, site ? "Publish again" : "Publish");
  return el("div", { className: "publish-section" },
    el("h3", {}, "New site (Cloudflare)"),
    el("p", { className: "hint" }, "Sends the score, one MP3 per part and the timing for the moving cursor, and lists the song for the choir."),
    el("label", {}, "Choir"),
    el("div", { className: "row" }, choir, btn),
    status);
}

// The local videos are a cache once YouTube has them (#371): 4K, ~150 MB a
// voice. Freeing asks YouTube first and deletes nothing unless every video is
// there; the server says which one is missing when it refuses.
const VIDEO_STATE = { uploaded: "on YouTube", changed: "rendered again since the upload",
  not_uploaded: "not uploaded" };

function freeSpaceSection(P, song, ups, recording, refresh) {
  const gb = (bytes) => (bytes / 2 ** 30).toFixed(2) + " GB";
  const rows = el("ul", { className: "videostate" }, ups.videos.map((v) =>
    el("li", { className: v.state },
      el("b", {}, v.part), " — ", VIDEO_STATE[v.state] || v.state,
      v.local ? ` · on disk (${(v.size / 2 ** 20).toFixed(0)} MB)` : " · not on disk")));
  const note = el("p", { className: "hint free-note" });
  const freed = song.record?.freed;
  if (freed && !ups.local_count) {
    note.textContent = `Freed ${gb(freed.bytes)} on ${new Date(freed.at * 1000).toLocaleDateString()}. `
      + "Recording again renders the videos afresh.";
  } else if (ups.local_count && !ups.complete) {
    note.textContent = "Every video has to be on YouTube before the local copies can go.";
  }
  const btn = el("button", { className: "free-videos", disabled: recording || !ups.can_free,
    onclick: async () => {
      if (!confirm(`Delete the ${ups.local_count} local video(s), ${gb(ups.local_bytes)}? `
        + "YouTube is checked first; recording again makes them back.")) return;
      btn.disabled = true;
      note.className = "hint free-note";
      note.textContent = "Checking YouTube…";
      try { await postJSON(`${P}/free-videos`); refresh(); }
      catch (e) {
        note.className = "hint free-note err";
        note.textContent = e.message;
        btn.disabled = false;
      }
    } }, ups.local_count ? `Free space (${gb(ups.local_bytes)})` : "Free space");
  return el("div", { className: "free-section" },
    el("h3", {}, "Local videos"), rows, el("div", { className: "row" }, btn), note);
}

// Which of the choir's playlists hold this song (#338). Read from YouTube each
// time the panel opens: the app keeps no record of it, and a playlist can be
// edited in YouTube's own app. A tick means every video of the song is in it.
function playlistSection(P, recording) {
  const box = el("div", { className: "playlists" });
  const section = el("div", { className: "playlist-section" },
    el("h3", {}, "Playlists"),
    el("p", { className: "hint" }, "Tick a playlist to put this song's videos in it; untick to take them out."),
    box);

  const load = async () => {
    box.replaceChildren(el("p", { className: "hint" }, "Reading playlists from YouTube…"));
    let data;
    try { data = await getJSON(`${P}/playlists`); }
    catch (e) { box.replaceChildren(el("p", { className: "banner err" }, e.message)); return; }
    box.replaceChildren(...data.playlists.map(row), addAnother(data.playlists));
  };

  const row = (p) => {
    const tick = el("input", { type: "checkbox", disabled: recording || !!p.error });
    tick.checked = p.total > 0 && p.count >= p.total;
    tick.indeterminate = p.count > 0 && p.count < p.total;
    const note = el("span", { className: "plnote" },
      p.error || (tick.indeterminate ? `${p.count} of ${p.total}` : ""));
    const line = el("div", { className: "plrow", "data-playlist": p.id },
      el("label", {}, tick, " ", p.title || p.id, " ", note),
      el("button", { className: "plhide", title: "Stop offering this playlist (nothing changes on YouTube)",
        onclick: async () => {
          try { await api(`/api/playlists/${encodeURIComponent(p.id)}`, { method: "DELETE" }); load(); }
          catch (e) { appendLog(e.message, true); }
        } }, "Hide"));
    tick.onchange = async () => {
      const member = tick.checked;
      tick.disabled = true;
      note.textContent = member ? "Adding…" : "Removing…";
      try {
        const now = await postJSON(`${P}/playlists`, { playlist_id: p.id, member, title: p.title });
        line.replaceWith(row(now));
      } catch (e) {
        appendLog(e.message, true);
        note.textContent = e.message;
        tick.checked = !member;
        tick.disabled = false;
      }
    };
    return line;
  };

  const addAnother = (shown) => {
    const pick = el("select", {}, el("option", { value: "" }, "Add another playlist…"));
    let loaded = false;
    const fill = async () => {
      if (loaded) return;
      loaded = true;
      try {
        const all = await getJSON("/api/youtube-playlists");
        const have = new Set(shown.map((p) => p.id));
        for (const p of all) if (!have.has(p.id)) pick.append(el("option", { value: p.id }, p.title));
      } catch (e) { appendLog(e.message, true); loaded = false; }
    };
    pick.onfocus = fill;
    pick.onpointerdown = fill;
    pick.onchange = async () => {
      if (!pick.value) return;
      try {
        await postJSON("/api/playlists", { playlist_id: pick.value,
          title: pick.options[pick.selectedIndex].textContent });
        load();
      } catch (e) { appendLog(e.message, true); }
    };
    return el("div", { className: "row" }, pick);
  };

  load();
  return section;
}

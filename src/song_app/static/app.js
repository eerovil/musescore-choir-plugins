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

function newSongDialog() {
  const name = el("input", { placeholder: "Song name, e.g. Laulun aika" });
  const per = el("input", { type: "checkbox" });
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
  pdf.onchange = sayRoute;
  xml.onchange = sayRoute;
  sayRoute();
  const create = el("button", { className: "primary", onclick: async () => {
    if (!name.value.trim()) { status.textContent = "Name is required."; return; }
    if (!pdf.files[0] && !xml.files[0]) {
      status.textContent = "A score PDF or a score file is required — give at least one.";
      return;
    }
    if (!voicing.value) { status.textContent = "Choose who sings it — it decides the part names."; return; }
    const fd = new FormData();
    fd.append("name", name.value.trim());
    fd.append("per_system", per.checked);
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
    el("div", { className: "row" }, per, el("span", {}, "Staves change parts per system (per-system mode)")),
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

  // On a phone the three panes cannot share the screen, so one is shown at a time and
  // this bar switches between them. It is in the DOM at every width — the stylesheet
  // hides it above the breakpoint, so the desktop layout is untouched and there is no
  // width-sniffing in here to disagree with the media query.
  const paneBtns = {};
  let pane = "panel";
  const showPane = (p) => {
    if (p !== "viewer") viewerEl._pausePreview();
    pane = p;
    for (const k of ["stages", "panel", "viewer"]) wsGrid.classList.toggle("m-" + k, k === p);
    for (const k in paneBtns) paneBtns[k].className = "mtab" + (k === p ? " active" : "");
    // A pane that was hidden has no width, so its PDF could not render while it was
    // away; now that it is on screen, let it.
    if (p === "viewer") viewerEl._wake();
  };
  const mobilebar = el("div", { className: "mobilebar" },
    [["stages", "Stages"], ["panel", "Panel"], ["viewer", "Score"]].map(([k, label]) =>
      (paneBtns[k] = el("button", { className: "mtab", onclick: () => showPane(k) }, label))));
  wsGrid.append(mobilebar);

  app.replaceChildren(wsGrid);
  setWide(localStorage.getItem("wsWide") === "1");
  showPane("panel");

  function drawStagebar() {
    const rec = song.record || {};
    const recorded = !!(rec.outputs && rec.outputs.length);
    const uploaded = !!(rec.uploads && rec.uploads.length);
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
      openPreview: () => { viewerEl._showFirst("preview"); showPane("viewer"); },
      // The panel is a task screen and the score is a full-screen visual, so a
      // panel that says "look at this" has to be able to put it in front of you.
      openDoc: (doc) => { viewerEl._showFirst(doc); showPane("viewer"); },
      pausePreview: () => viewerEl._pausePreview(),
      previewInputsChanged: () => viewerEl._syncPreview(),
      setPreviewSettings: (settings) => { recordPreviewSettings = settings; },
    });
  }

  function selectStage(stage) {
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
    drawPanel();
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

// Render `url` into `view` (a scrollable div), preserving its current scrollTop.
async function renderPdf(view, url) {
  const token = (view._tok = (view._tok || 0) + 1);
  const lib = await ensurePdfjs();
  const pdf = await lib.getDocument(url).promise;
  if (view._tok !== token) return; // superseded by a newer render
  const width = view.clientWidth || 600;
  const canvases = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    if (view._tok !== token) return;
    const base = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: (width - 14) / base.width });
    const c = el("canvas", { className: "pdfpage" });
    c.width = vp.width; c.height = vp.height;
    await page.render({ canvasContext: c.getContext("2d"), viewport: vp }).promise;
    if (view._tok !== token) return;
    canvases.push(c);
  }
  const top = view.scrollTop;          // same content height → restoring is exact
  view.replaceChildren(...canvases);
  view.scrollTop = top;
}

// Render with pdf.js; fall back to a native iframe if pdf.js can't load (offline).
async function mountPdf(view, url) {
  try {
    await renderPdf(view, url);
    view._renderedUrl = url;
  } catch {
    view.replaceChildren(el("iframe", { className: "pdffallback",
      src: url + "#navpanes=0&view=FitH" }));
    view._renderedUrl = url;
  }
}


// Focusing a lyric cell asks the viewer to show that printed system, full width.
// The sidebar is too narrow to read a system in; the viewer is the space for it.
const SYSTEM_EVENT = "song-system";
const showSystem = (index) =>
  window.dispatchEvent(new CustomEvent(SYSTEM_EVENT, { detail: { index } }));

// ---- Compare: each printed system above the same system of the cleaned score ----
// Reviewing means checking one against the other, and they cannot simply be laid
// side by side: the cleaned score has twice the staves, so its systems are far
// taller. Cut both into systems and pair them up and the comparison is per line.
async function compareView(view, slug) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  view.replaceChildren(el("p", { className: "muted" }, "Pairing systems…"));
  let data;
  try {
    data = await (await fetch(`${P}/compare`)).json();
  } catch {
    view.replaceChildren(el("p", { className: "warn" }, "Could not pair the systems."));
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
  view.replaceChildren(...rows.map((r) => byIndex[r.index] = el("div", { className: "cmprow" },
    el("div", { className: "cmphead" },
      `System ${r.index} — measures ${r.measure_start}–${r.measure_end}`),
    el("div", { className: "cmplabel" }, "scan"),
    el("img", { className: "cmpimg", loading: "lazy",
                src: `${P}/system/${r.index}?dpi=300`, alt: `printed system ${r.index}` }),
    el("div", { className: "cmplabel" }, "cleaned"),
    el("img", { className: "cmpimg", loading: "lazy",
                src: `${P}/cleaned-system/${r.index}?dpi=300`, alt: `cleaned system ${r.index}` }),
  )));

  // Typing lyrics for a system should put that system in front of you, scan and
  // result together — that is the pair you are checking the words against.
  view._focusSystem = (n) => {
    const row = byIndex[n];
    if (!row) return;
    for (const el_ of Object.values(byIndex)) el_.classList.remove("cmpon");
    row.classList.add("cmpon");
    row.scrollIntoView({ block: "start", behavior: "smooth" });
  };
}

// ---- Scan vs page: each printed system above what the scan read off it -------
// The same idiom as Compare, one stage earlier and against the parse rather than
// the cleaned score. Cropping first is not a nicety: a whole A4 rendered small
// enough to look at cannot show a slur, which is how a confident and wrong
// reading got made here once already.
function scannedView(view, song, slug) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  const st = song.scan_status || {};
  const errors = st.errors || {};
  const fresh = new Set(st.new_since_ok || []);
  const rows = [];
  const byIndex = {};
  for (let i = 1; i <= (st.systems || 0); i++) {
    const bad = errors[String(i)];
    const label = fresh.has(i) && st.ever_approved
      ? el("span", { className: "newbadge" }, "new since your OK") : "";
    rows.push(byIndex[i] = el("div", { className: "cmprow" },
      el("div", { className: "cmphead" }, `System ${i}`, label),
      el("div", { className: "cmplabel" }, "page"),
      el("img", { className: "cmpimg", loading: "lazy",
                  src: `${P}/system/${i}?dpi=300`, alt: `printed system ${i}` }),
      el("div", { className: "cmplabel" }, "scan"),
      // A hole shows its reason where its music would be. Leaving the row out
      // instead is how a score quietly short of a system reads as complete.
      bad ? el("div", { className: "cmperr" }, "Could not be read: " + bad)
          : el("img", { className: "cmpimg", loading: "lazy",
                        src: `${P}/scan-system/${i}?dpi=200`,
                        alt: `scanned system ${i}` }),
    ));
  }
  view.replaceChildren(...(rows.length ? rows
    : [el("p", { className: "warn" }, "Nothing has been read yet.")]));
  view._focusSystem = (n) => {
    const row = byIndex[n];
    if (!row) return;
    for (const other of Object.values(byIndex)) other.classList.remove("cmpon");
    row.classList.add("cmpon");
    row.scrollIntoView({ block: "start", behavior: "smooth" });
  };
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
      status,
      el("span", { className: "muted" },
        " Drag an edge to adjust; click the page for a new system; × removes one."),
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
          v._setSystem = (n) => {
            v._n = n;
            v.replaceChildren(
              el("div", { className: "muted" }, `Printed system ${n}`),
              el("img", { src: `/api/songs/${encodeURIComponent(slug)}/system/${n}?dpi=400` }),
            );
          };
          v._setSystem(v._n || 1);
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
          v.append(preview);
        }
        else v._url = docUrl(slug, doc, song.cleaned_fingerprint);
      }
      for (const d in frames) frames[d].style.display = d === doc ? "" : "none";
      for (const k in btns) btns[k].className = k === doc ? "vtab active" : "vtab";
      ensureRendered(doc); // now visible → has width
    };
    const tabRow = tabs.map(([k, label]) => (btns[k] = el("button", {
      className: "vtab", onclick: () => show(k),
    }, label)));

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

    const ctrl = panes.length === 1
      ? el("button", { className: "vtab split", title: "Split view",
          onclick: () => { panes.push(keys.find((k) => k !== panes[0]) || panes[0]); rebuild(); } }, "⇆ Split")
      : el("button", { className: "vtab close", title: "Close this pane",
          onclick: () => { panes.splice(i, 1); rebuild(); } }, "✕");

    const bar = el("div", { className: "viewtabs" }, ...tabRow, el("span", { className: "spacer" }), ctrl);
    show(panes[i]);
    selectors.push(show);
    previewPausers.push(() => frames.preview?._pause?.());
    previewDestroyers.push(() => frames.preview?._destroy?.());
    previewSyncers.push(() => frames.preview?._sync?.());
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
  let builtScan = `${song.scan_status?.revision}:${song.scan_status?.approved}`;
  root._refreshScan = () => {
    const now = `${song.scan_status?.revision}:${song.scan_status?.approved}`;
    if (now === builtScan) return;
    builtScan = now;
    scanRefreshers.forEach((f) => f());
  };
  root._refreshFp = (fp) => refreshers.forEach((f) => f(fp));
  root._wake = () => wakers.forEach((f) => f());
  root._pausePreview = () => previewPausers.forEach((pause) => pause());
  root._destroyPreview = () => previewDestroyers.forEach((destroy) => destroy());
  root._syncPreview = () => previewSyncers.forEach((sync) => sync());
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
  for (const gone of song.scan_discarded || [])
    panel.append(el("div", { className: "banner" },
      `Discarded ${gone}: what it was made from has changed.`));

  const rerun = async (systems) => {
    appendLog(systems ? "Re-reading system(s) " + systems.join(", ") + "…"
                      : "Starting scan…");
    try { await postJSON(`${P}/scan`, systems ? { systems } : {}); refresh(); }
    catch (e) { appendLog(e.message, true); }
  };
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

  if (running)
    panel.append(el("div", { className: "banner" },
      "● Scanning… recent messages are saved below."));
  else if (job?.status === "failed")
    panel.append(el("div", { className: "banner err" }, "Last scan failed: " + job.error));

  // The gate. One OK for the whole song, and only when there is a whole score to
  // approve — per-system ticking was rejected as friction that produces false
  // diligence rather than more looking.
  if (st.complete && !running)
    panel.append(scanApproval(st, song, P, refresh, actions, openScanned));

  panel.append(makeLog(job));
}

function scanApproval(st, song, P, refresh, actions, openScanned) {
  if (st.approved)
    return el("div", { className: "banner good" },
      "You have said this reading of the page is right. The song is on Clean.");
  const fresh = st.new_since_ok || [];
  const okBtn = el("button", { className: "primary", onclick: async () => {
    okBtn.disabled = true;
    try {
      Object.assign(song, await postJSON(`${P}/approve-scan`, { revision: st.revision }));
      // Saying it is right is also saying "get on with it", so the panel follows
      // the song to the stage it just unlocked rather than sitting on a done one.
      if (actions?.selectStage) actions.selectStage("clean");
      else refresh();
    } catch (e) { okBtn.disabled = false; appendLog(e.message, true); }
  }}, "This reading is right — continue to Clean");
  return el("section", { className: "scanok" },
    el("h3", {}, "Say it is right"),
    el("p", { className: "hint" },
      "Nothing checks this for you. The parse that hurts is the tidy-looking one, "
      + "so compare each system against the page before you press it."),
    // Re-reading a system lapses the OK, and the systems that changed are where
    // to look. A hint, not per-system bookkeeping.
    st.ever_approved
      ? el("p", { className: "warn" }, fresh.length
        ? `Your OK lapsed. Changed since it: system(s) ${fresh.join(", ")}.`
        : "Your OK lapsed because the scan changed.")
      : "",
    el("div", { className: "row" }, okBtn,
      el("button", { onclick: openScanned }, "Compare with the page")));
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
      holder.replaceChildren(...grid.map((sys) => sysBlock(sys)));

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
        "data-sys": sys.system, "data-staff": st.staff_id,
        "data-hint": st.voices > 1 ? "e.g. T1, T2 (- = silent)" : "e.g. T1 (- = silent)",
      }))));
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
  const pending = song.pending_fixes || [];
  if (pending.length) {
    panel.append(el("div", { className: "issue" },
      el("div", { className: "top" }, el("span", {}, el("span", { className: "kind" }, "fixes.json — not applied automatically"))),
      ...pending.map((t) => el("div", { className: "detail" }, t))));
  }
  const issues = song.open_issues || [];
  if (!issues.length) {
    panel.append(el("p", { className: "empty" }, "✓ No issues. Ready for lyrics."));
  } else {
    panel.append(...issues.map((i) =>
      el("div", { className: "issue" },
        el("div", { className: "top" },
          el("span", {}, el("span", { className: "m" }, `m${i.measure}`), "  ", el("span", { className: "kind" }, i.kind)),
          el("button", { onclick: async () => { await postJSON(`${P}/issues/${i.id}/dismiss`); refresh(); } }, "Dismiss")),
        el("div", { className: "detail" }, `${i.staff}: ${i.detail}`))));
  }
  panel.append(el("div", { className: "row" },
    el("button", { className: "primary", onclick: () => postJSON(`${P}/open-score`) }, "Open in MuseScore"),
    el("button", { onclick: async () => { await postJSON(`${P}/rescan`); refresh(); } }, "Re-check now")));
  slurRecorder(panel, song, P, refresh);
}

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
  const swap = (m) => { localStorage.setItem("lyricMode", m); panel.replaceChildren(); panelLyrics(panel, song, P, refresh); };
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

async function lyricsPaste(panel, song, P, refresh) {
  const warns = lyricMismatches(song);
  if (warns.length) {
    panel.append(el("p", { className: "sub" }, "Mismatches (often a note problem — check the measure in MuseScore):"),
      el("ul", { className: "warnlist" }, warns.map((w) => el("li", {}, w.message))));
  }
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
      el("button", { className: "primary", onclick: async () => {
        appendLog("Importing lyrics…");
        try {
          const fresh = await postJSON(`${P}/lyrics`, { json: ta.value });
          Object.assign(song, fresh);
          const w = song.lyrics?.warnings || [];
          if (w.length) appendLog(`Imported with ${w.length} warning(s).`);
          else appendLog("Imported cleanly. Ready for review.");
          refresh();
        } catch (e) { appendLog(e.message, true); }
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
  const warns = lyricMismatches(song);
  // Attach a mismatch to the system where its line STARTS (a line can span several
  // systems if the part is blank in later ones); show the full measure range.
  const cellWarns = (sys, p) => warns.filter((w) =>
    (w.staff_ids || []).includes(p.id) && w.measure_start >= sys.start && w.measure_start <= sys.end);

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
        if (shown) ta.onfocus = () => showSystem(shown);
        return el("div", { className: "lyrow" },
          el("label", {}, `${p.name} · ${capacities?.[sys.index]?.[p.name] ?? 0} lyric slots`), ta,
          ...cellWarns(sys, p).map((w) => el("div", { className: "lyerr" },
            `⚠ m${w.measure_start}–${w.measure_end}`
            + `${w.measure_end > sys.end ? " (spans later systems)" : ""}: `
            + `${w.message.split("): ").pop()}`)));
      }))));
  toggle.textContent = Object.keys(byStart).length
    ? (showScore ? "Hide the score" : "Show the score")
    : "";
  toggle.style.display = Object.keys(byStart).length ? "" : "none";
  holder.querySelectorAll("textarea").forEach(autoGrow); // size to content (no scroll)
  if (lyricScroll != null) { panel.scrollTop = lyricScroll; lyricScroll = null; } // restore after re-import

  const doImport = async (btn) => {
    const map = {};
    panel.querySelectorAll("textarea[data-sys]").forEach((t) => {
      (map[t.dataset.sys] ||= {})[t.dataset.part] = t.value.trim();
    });
    if (!Object.values(map).some((row) => Object.values(row).some((t) => t))) {
      appendLog("Nothing typed yet.", true); return;
    }
    btn.disabled = true;
    lyricScroll = panel.scrollTop; // preserve scroll across the re-render
    appendLog("Importing lyrics…");
    try {
      const fresh = await postJSON(`${P}/lyrics`, { cells: map });
      Object.assign(song, fresh);
      const w = song.lyrics?.warnings || [];
      appendLog(w.length ? `Imported with ${w.length} warning(s).` : "Imported cleanly. Ready for review.");
      refresh(); // re-renders with updated inline errors
    } catch (e) { btn.disabled = false; appendLog(e.message, true); }
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
  const hardwareEncoding = el("input", {
    type: "checkbox", checked: rec.hardware_encoding !== false
  });
  const bpm = el("input", {
    type: "number", value: rec.bpm ?? 80, min: 20, max: 300, step: 1,
    style: "width:120px"
  });
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
    ] : []));
  const scrollAdvanced = el("div", {},
    el("label", {}, "Vertical margins"),
    el("div", { className: "row" },
      el("span", {}, "Top margin"), topMargin, el("span", {}, "%")),
    el("div", { className: "row" },
      el("span", {}, "Bottom margin"), bottomMargin, el("span", {}, "%")),
    el("p", { className: "hint" }, "0 = current layout; positive adds white space; negative crops that edge"),
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
  const previewBtn = el("button", { className: "preview-action", onclick: actions.openPreview }, "Preview");

  actions.setPreviewSettings(() => ({
    quality: quality.value,
    top_margin: Number(topMargin.value) || 0,
    bottom_margin: Number(bottomMargin.value) || 0,
    ...(song.needs_initial_bpm ? { bpm: Number(bpm.value) } : {}),
  }));
  for (const control of [quality, topMargin, bottomMargin, bpm]) {
    control.addEventListener("input", actions.previewInputsChanged);
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
    el("div", { className: "record-actions" }, previewBtn, runBtn),
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

  if (recording) {
    panel.append(el("div", { className: "banner" }, "● Working… leave this running."));
  } else if (uploads.length) {
    panel.append(el("div", { className: "banner good" }, "✓ Uploaded to YouTube."));
  }
  if (rec.error) {
    panel.append(el("div", { className: "banner err" }, "Last run failed: " + rec.error));
  } else if (song.jobs?.upload?.status === "failed") {
    panel.append(el("div", { className: "banner err" },
      "Last upload failed: " + song.jobs.upload.error));
  }
  if (!recorded) {
    panel.append(el("p", { className: "hint" }, "Nothing to upload yet — record the videos first."));
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

  const uploadBtn = el("button", { className: "primary", disabled: recording || !recorded,
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
    makeLog(song.jobs?.upload || song.jobs?.render));

  if (uploads.length) {
    panel.append(
      el("h3", {}, "Uploaded videos"),
      el("ul", { className: "uploads" }, uploads.map((u) =>
        el("li", {}, el("a", { href: u.url, target: "_blank", rel: "noopener" }, u.title || u.url)))),
      el("div", { className: "row" },
        el("button", { disabled: recording, onclick: async () => {
          if (!confirm("Delete these videos from YouTube? You can then re-upload.")) return;
          appendLog("Deleting from YouTube…");
          try { await postJSON(`${P}/youtube-delete`); appendLog("Deleted."); refresh(); }
          catch (e) { appendLog(e.message, true); }
        }}, "Delete from YouTube")));
  }
}

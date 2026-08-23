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

const STAGE_LABEL = { register: "Start", clean: "Clean", fix: "Fix", lyrics: "Lyrics", review: "Review", record: "Record", upload: "Upload" };

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
  const xml = el("input", { type: "file", accept: ".mscx,.mscz,.musicxml,.xml" });
  const pdf = el("input", { type: "file", accept: ".pdf" });
  const status = el("p", { className: "hint" });
  const create = el("button", { className: "primary", onclick: async () => {
    if (!name.value.trim() || !xml.files[0]) { status.textContent = "Name and a score file are required."; return; }
    const fd = new FormData();
    fd.append("name", name.value.trim());
    fd.append("per_system", per.checked);
    fd.append("xml", xml.files[0]);
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
    el("label", {}, "Score file (.mscx / .mscz / .musicxml / .xml)"), xml,
    el("label", {}, "Score PDF (recommended — used for lyrics)"), pdf,
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
  const panes = [song.has_pdf ? "pdf" : "original"]; // 1 or 2 docs shown side by side
  let viewFp = song.cleaned_fingerprint; // viewer is only rebuilt when this changes

  // Build the shell once. The viewer (and its rendered previews) is NOT recreated on
  // stage or panel changes, so the previews keep their loaded state and scroll position.
  const stagebarEl = el("div", { className: "stagebar" });
  const panelEl = el("div", { className: "panel" });
  let viewerEl = viewer(song, slug, panes, rebuildViewer);
  const wsGrid = el("div", { className: "ws" }, stagebarEl, panelEl, viewerEl);
  // Reviewing means reading two scores side by side; the rail and panel are just
  // in the way then. Remembered, because it is a mode you stay in.
  const setWide = (on) => {
    wsGrid.className = on ? "ws wide" : "ws";
    localStorage.setItem("wsWide", on ? "1" : "0");
    wideBtn.textContent = on ? "❯ Show panel" : "❮ Hide panel";
    wideBtn.title = on ? "show the stage panel" : "hide the stage panel for more room";
  };
  const wideBtn = el("button", { className: "widetoggle",
    onclick: () => setWide(!wsGrid.classList.contains("wide")) });
  wsGrid.append(wideBtn);
  app.replaceChildren(wsGrid);
  setWide(localStorage.getItem("wsWide") === "1");

  function drawStagebar() {
    const rec = song.record || {};
    const recorded = !!(rec.outputs && rec.outputs.length);
    const uploaded = !!(rec.uploads && rec.uploads.length);
    const done = (st, i) =>
      i < song.stage_index || (st === "record" && recorded) || (st === "upload" && uploaded);
    stagebarEl.replaceChildren(...song.stages.map((st, i) => el("div", {
      className: "step " + (st === view ? "active " : "") + (done(st, i) ? "done" : ""),
      onclick: () => { view = st; drawStagebar(); drawPanel(); },
    }, STAGE_LABEL[st] || st)));
  }

  function drawPanel() {
    panelEl.replaceChildren();
    renderPanel(panelEl, view, song, slug, refresh);
  }

  function rebuildViewer() {
    const next = viewer(song, slug, panes, rebuildViewer);
    wsGrid.replaceChild(next, viewerEl);
    viewerEl = next;
    builtTabs = tabKeys();
  }

  const tabKeys = () => viewerTabs(song).map(([k]) => k).join(",");
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
    }
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

function viewerTabs(song) {
  const tabs = [];
  if (song.has_pdf) tabs.push(["pdf", "Original PDF"]);
  if (song.has_pdf) tabs.push(["systems", "Systems"]);
  if (song.systems) tabs.push(["system", "One system"]);
  if (song.systems && song.has_cleaned) tabs.push(["compare", "Compare"]);
  tabs.push(["original", "Original XML"]);
  if (song.has_cleaned) {
    tabs.push(["cleaned_nolyrics", "Cleaned MSCX"]);
    // Only once there are lyrics to see. Offering it beforehand renders a score
    // identical to the tab next to it, with nothing to say why.
    if (song.lyrics) tabs.push(["cleaned", "Cleaned MSCX with lyrics"]);
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
  view.replaceChildren(...rows.map((r) => el("div", { className: "cmprow" },
    el("div", { className: "cmphead" },
      `System ${r.index} — measures ${r.measure_start}–${r.measure_end}`),
    el("div", { className: "cmplabel" }, "scan"),
    el("img", { className: "cmpimg", loading: "lazy",
                src: `${P}/system/${r.index}?dpi=300`, alt: `printed system ${r.index}` }),
    el("div", { className: "cmplabel" }, "cleaned"),
    el("img", { className: "cmpimg", loading: "lazy",
                src: `${P}/cleaned-system/${r.index}?dpi=300`, alt: `cleaned system ${r.index}` }),
  )));
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
            onmousedown: (ev) => startDrag(ev, img, b, edge, box),
          }));
        }
        return box;
      }));
    }
  }

  function startDrag(ev, img, band, edge, box) {
    ev.preventDefault(); ev.stopPropagation();
    const rect = img.getBoundingClientRect();
    if (!rect.height) return;                        // image not laid out yet
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
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      drawBands();                                   // re-label and re-order once
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
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
function viewer(song, slug, panes, rebuild) {
  const tabs = viewerTabs(song);
  const keys = tabs.map(([k]) => k);
  for (let i = 0; i < panes.length; i++) if (!keys.includes(panes[i])) panes[i] = keys[0];
  const refreshers = [];

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
      else requestAnimationFrame(() => ensureRendered(doc)); // wait for layout (e.g. after split)
    };
    const show = (doc) => {
      panes[i] = doc;
      if (!frames[doc]) {
        const v = el("div", { className: "pdfview" });
        frames[doc] = v;
        body.append(v);
        if (doc === "systems") { v._systems = true; systemsEditor(v, slug); }
        else if (doc === "compare") { v._systems = true; v.className = "pdfview compare"; compareView(v, slug); }
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
        else v._url = docUrl(slug, doc, song.cleaned_fingerprint);
      }
      for (const d in frames) frames[d].style.display = d === doc ? "" : "none";
      for (const k in btns) btns[k].className = k === doc ? "vtab active" : "vtab";
      ensureRendered(doc); // now visible → has width
    };
    const tabRow = tabs.map(([k, label]) => (btns[k] = el("button", {
      className: "vtab", onclick: () => show(k),
    }, label)));

    if (keys.includes("system")) {
      const onAsk = (ev) => {
        const n = ev.detail && ev.detail.index;
        if (!n) return;
        // Only the first pane follows the cursor, so a split can keep a second
        // document in view while the other tracks what is being typed.
        if (i !== 0) return;
        show("system");
        frames.system._setSystem(n);
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
  root._refreshFp = (fp) => refreshers.forEach((f) => f(fp));
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

function makeLog() {
  logBox = el("div", { className: "log" });
  return logBox;
}

// ---- per-stage panels ----------------------------------------------------
function renderPanel(panel, view, song, slug, refresh) {
  logBox = null;
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  if (view === "register") return panelRegister(panel, song, P, refresh);
  if (view === "clean") return panelClean(panel, song, slug, P, refresh);
  if (view === "fix") return panelFix(panel, song, P, refresh);
  if (view === "lyrics") return panelLyrics(panel, song, P, refresh);
  if (view === "review") return panelReview(panel, song, P, refresh);
  if (view === "record") return panelRecord(panel, song, P, refresh);
  if (view === "upload") return panelUpload(panel, song, P, refresh);
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
  panel.append(makeLog());
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
  return el("div", { className: "sysblock" },
    el("h4", {}, `System ${sys.system + 1} — measures ${sys.measure_start}–${sys.measure_end}`),
    el("table", { className: "grid" },
      el("tr", {}, el("th", {}, "Staff"), el("th", {}, "Voices"), el("th", {}, "Content"), el("th", {}, "Part names")),
      rows));
}

function panelFix(panel, song, P, refresh) {
  panel.append(el("h2", {}, "Fix"),
    el("p", { className: "sub" }, "OCR damage the auto-fixers couldn't repair. Fix in MuseScore, save, and it re-checks automatically."));
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
  const { parts, systems, cells } = grid;
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
          el("label", {}, p.name), ta,
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

function panelReview(panel, song, P, refresh) {
  panel.append(el("h2", {}, "Review"),
    el("p", { className: "sub" }, "Final check of notes + lyrics before producing tracks."),
    el("div", { className: "row" },
      el("button", { className: "primary", onclick: () => postJSON(`${P}/open-score`) }, "Open in MuseScore"),
      el("button", { onclick: async () => { await postJSON(`${P}/stage/record`); refresh(); } }, "Looks good → Record")));
}

function panelRecord(panel, song, P, refresh) {
  const rec = song.record || {};
  const recording = song.recording;
  const recorded = !!(rec.outputs && rec.outputs.length);
  // Two ways to make the videos. The scrolling renderer draws them from the score
  // and is the default; the screen recorder drives MuseScore and needs macOS.
  let renderer = rec.renderer || "scroll";

  panel.append(el("h2", {}, "Record"));
  const sub = el("p", { className: "sub" }, "");
  panel.append(sub);

  if (recording) {
    panel.append(el("div", { className: "banner" }, "● Rendering… leave this running."));
  } else if (recorded) {
    panel.append(el("div", { className: "banner good" },
      "✓ Ready" + (rec.renderer === "screen" ? " (screen recording)." : ".")));
  }
  if (rec.error) {
    panel.append(el("div", { className: "banner err" }, "Last run failed: " + rec.error));
  }

  const scrollRadio = el("input", { type: "radio", name: "renderer", checked: renderer === "scroll" });
  const screenRadio = el("input", { type: "radio", name: "renderer", checked: renderer === "screen" });
  const quality = el("select", {},
    el("option", { value: "4k", selected: (rec.quality || "4k") === "4k" }, "4K, 60fps"),
    el("option", { value: "1080p", selected: rec.quality === "1080p" }, "1080p, 30fps (faster)"));
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

  const runBtn = el("button", { className: "primary", disabled: recording, onclick: () =>
    renderer === "scroll"
      ? post({ quality: quality.value }, "Rendering the scrolling video…")
      : post({ audio_delay_ms: Number(delay.value) || 1300,
               redo_mp3: redoMp3.checked, redo_video: redoVideo.checked }, "Starting…") }, "");
  const remergeBtn = el("button", { disabled: recording, onclick: () =>
    post({ merge_only: true, audio_delay_ms: Number(delay.value) || 1300 },
         "Re-merging with new offset…") }, "Re-merge only (apply offset)");

  const scrollOpts = el("div", {},
    el("label", {}, "Size"),
    el("div", { className: "row" }, quality,
      el("span", { className: "hint" }, "4K takes roughly 1.5 minutes of CPU per minute of music")));
  const screenOpts = el("div", {},
    el("label", {}, "Audio sync offset (ms)"),
    el("div", { className: "row" }, delay,
      el("span", { className: "hint" }, "shift audio vs. video; re-merge to apply")),
    el("div", { className: "row" }, redoMp3, el("span", {}, "Re-export MP3")),
    el("div", { className: "row" }, redoVideo, el("span", {}, "Re-record video")),
    el("div", { className: "row" }, remergeBtn));

  const applyRenderer = () => {
    scrollRadio.checked = renderer === "scroll";
    screenRadio.checked = renderer === "screen";
    scrollOpts.style.display = renderer === "scroll" ? "" : "none";
    screenOpts.style.display = renderer === "screen" ? "" : "none";
    runBtn.textContent = renderer === "scroll" ? "Render videos" : "Run recording";
    sub.textContent = renderer === "scroll"
      ? "Draw the scrolling score straight from the notes — nothing on screen, runs unattended."
      : "Export per-voice audio, record + merge the play-along video. macOS only.";
  };
  const choose = (which) => { renderer = which; applyRenderer(); };

  panel.append(
    el("label", {}, "How to make the videos"),
    el("div", { className: "row" }, scrollRadio,
      el("span", { onclick: () => choose("scroll") }, "Scrolling score "),
      el("span", { className: "hint" }, "(default)")),
    el("div", { className: "row" }, screenRadio,
      el("span", { onclick: () => choose("screen") }, "Screen recording "),
      el("span", { className: "hint" }, "(MuseScore + QuickRecorder, macOS)")),
    scrollOpts, screenOpts,
    el("div", { className: "row" }, runBtn),
    makeLog());

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
    el("p", { className: "sub" }, "Upload the recorded videos to YouTube and (optionally) add them to a playlist."));

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
    makeLog());

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

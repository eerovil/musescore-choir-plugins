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
  app.replaceChildren(wsGrid);

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
  }

  async function refresh() {
    const hadCleaned = song.has_cleaned;
    const fresh = await getJSON(`/api/songs/${encodeURIComponent(slug)}`);
    Object.assign(song, fresh);
    drawStagebar();
    drawPanel();
    if (song.has_cleaned !== hadCleaned) {
      // The tab set changed (cleaned docs appeared/vanished) → structural rebuild.
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
  tabs.push(["original", "Original XML"]);
  if (song.has_cleaned) {
    tabs.push(["cleaned_nolyrics", "Cleaned MSCX"]);
    tabs.push(["cleaned", "Cleaned MSCX with lyrics"]);
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
      if (!v || v._url === v._renderedUrl) return;
      if (v.style.display === "none") return;      // render when shown
      if (v.clientWidth > 0) mountPdf(v, v._url);
      else requestAnimationFrame(() => ensureRendered(doc)); // wait for layout (e.g. after split)
    };
    const show = (doc) => {
      panes[i] = doc;
      if (!frames[doc]) {
        const v = el("div", { className: "pdfview" });
        v._url = docUrl(slug, doc, song.cleaned_fingerprint);
        frames[doc] = v;
        body.append(v);
      }
      for (const d in frames) frames[d].style.display = d === doc ? "" : "none";
      for (const k in btns) btns[k].className = k === doc ? "vtab active" : "vtab";
      ensureRendered(doc); // now visible → has width
    };
    const tabRow = tabs.map(([k, label]) => (btns[k] = el("button", {
      className: "vtab", onclick: () => show(k),
    }, label)));

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
            if (v) carry = v;
            else inp.placeholder = carry || inp.dataset.hint;
            // flag staves that will be dropped (no value and nothing to inherit)
            inp.classList.toggle("unset", !v && !carry);
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
            if (v) carry = v;
            else if (!carry) out.push(`staff ${inp.dataset.staff} · system ${+inp.dataset.sys + 1}`);
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
        value: st.answer || "", placeholder: st.voices > 1 ? "e.g. T1, T2 (type to set)" : "e.g. T1 (type to set)",
        "data-sys": sys.system, "data-staff": st.staff_id,
        "data-hint": st.voices > 1 ? "e.g. T1, T2 (type to set)" : "e.g. T1 (type to set)",
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

// Parse an import warning like "Measures 16-19 (staffs 1, 2): too many tokens …".
function parseLyricWarning(w) {
  const m = w.match(/^Measures (\d+)-(\d+) \(staffs ([\d,\s]+)\): (.+)$/);
  if (!m) return null;
  return {
    mStart: +m[1], mEnd: +m[2],
    staffIds: m[3].split(",").map((s) => +s.trim()).filter(Number.isFinite),
    msg: m[4],
  };
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

async function lyricsPaste(panel, song, P, refresh) {
  const warns = song.lyrics?.warnings || [];
  if (warns.length) {
    panel.append(el("p", { className: "sub" }, "Mismatches (often a note problem — check the measure in MuseScore):"),
      el("ul", { className: "warnlist" }, warns.map((w) => el("li", {}, w))));
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
  const holder = el("div", {}, el("p", { className: "hint" }, "Loading…"));
  panel.append(holder, makeLog());

  let grid;
  try { grid = await getJSON(`${P}/lyric-grid`); }
  catch (e) { holder.replaceChildren(el("p", { className: "err" }, e.message)); return; }
  const { parts, systems, prefill } = grid;
  const cellText = (si, name) => (prefill?.[si]?.[name]) || "";
  const warns = (song.lyrics?.warnings || []).map(parseLyricWarning).filter(Boolean);
  // Attach a warning to the system where its line STARTS (a line can span several
  // systems if the part is blank in later ones); show the full measure range.
  const cellWarns = (sys, p) => warns.filter((w) =>
    w.staffIds.includes(p.id) && w.mStart >= sys.start && w.mStart <= sys.end);

  holder.replaceChildren(...systems.map((sys) =>
    el("div", { className: "sysblock" },
      el("h4", {}, `System ${sys.index + 1} — measures ${sys.start}–${sys.end}`),
      ...parts.map((p) => {
        const ta = el("textarea", { rows: 1, value: cellText(sys.index, p.name),
          "data-sys": sys.index, "data-part": p.name });
        ta.oninput = () => autoGrow(ta);
        return el("div", { className: "lyrow" },
          el("label", {}, p.name), ta,
          ...cellWarns(sys, p).map((w) => el("div", { className: "lyerr" },
            `⚠ m${w.mStart}–${w.mEnd}${w.mEnd > sys.end ? " (spans later systems)" : ""}: ${w.msg}`)));
      }))));
  holder.querySelectorAll("textarea").forEach(autoGrow); // size to content (no scroll)
  if (lyricScroll != null) { panel.scrollTop = lyricScroll; lyricScroll = null; } // restore after re-import

  const doImport = async (btn) => {
    const map = {};
    panel.querySelectorAll("textarea[data-sys]").forEach((t) => {
      (map[t.dataset.sys] ||= {})[t.dataset.part] = t.value.trim();
    });
    const data = [];
    for (const sys of systems) {
      const lyrics = [];
      for (const p of parts) {
        const t = map[sys.index]?.[p.name];
        if (t) lyrics.push({ parts: [p.name], text: t });
      }
      if (lyrics.length) data.push({ measure_start: sys.start, lyrics });
    }
    if (!data.length) { appendLog("Nothing typed yet.", true); return; }
    btn.disabled = true;
    lyricScroll = panel.scrollTop; // preserve scroll across the re-render
    appendLog("Importing lyrics…");
    try {
      const fresh = await postJSON(`${P}/lyrics`, { json: JSON.stringify(data) });
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
  panel.append(el("h2", {}, "Record"),
    el("p", { className: "sub" }, "Export per-voice audio, record + merge the play-along video. macOS only."));

  const rec = song.record || {};
  const recording = song.recording;
  const recorded = !!(rec.outputs && rec.outputs.length);

  if (recording) {
    panel.append(el("div", { className: "banner" }, "● Recording in progress… leave this running."));
  } else if (recorded) {
    panel.append(el("div", { className: "banner good" }, "✓ Recorded."));
  }
  if (rec.error) {
    panel.append(el("div", { className: "banner err" }, "Last run failed: " + rec.error));
  }

  const delay = el("input", { type: "number", value: rec.audio_delay_ms ?? 1300, step: 50, style: "width:120px" });
  const redoMp3 = el("input", { type: "checkbox" });
  const redoVideo = el("input", { type: "checkbox" });

  const post = async (extra, msg) => {
    appendLog(msg);
    try {
      await postJSON(`${P}/record`, { audio_delay_ms: Number(delay.value) || 1300, ...extra });
      refresh();
    } catch (e) { appendLog(e.message, true); }
  };

  const runBtn = el("button", { className: "primary", disabled: recording,
    onclick: () => post({ redo_mp3: redoMp3.checked, redo_video: redoVideo.checked }, "Starting…") },
    "Run recording");
  const remergeBtn = el("button", { disabled: recording,
    onclick: () => post({ merge_only: true }, "Re-merging with new offset…") },
    "Re-merge only (apply offset)");

  panel.append(
    el("label", {}, "Audio sync offset (ms)"),
    el("div", { className: "row" }, delay, el("span", { className: "hint" }, "shift audio vs. video; re-merge to apply")),
    el("div", { className: "row" }, redoMp3, el("span", {}, "Re-export MP3")),
    el("div", { className: "row" }, redoVideo, el("span", {}, "Re-record video")),
    el("div", { className: "row" }, runBtn, remergeBtn),
    makeLog());

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

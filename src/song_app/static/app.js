"use strict";

const app = document.getElementById("app");
const crumb = document.getElementById("crumb");

// ---- tiny helpers --------------------------------------------------------
const el = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
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

const STAGE_LABEL = { register: "Start", clean: "Clean", fix: "Fix", lyrics: "Lyrics", review: "Review", record: "Record" };

// ---- router --------------------------------------------------------------
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);

function route() {
  const m = location.hash.match(/^#\/song\/(.+)$/);
  if (m) renderWorkspace(decodeURIComponent(m[1]));
  else renderLibrary();
}

// ---- library -------------------------------------------------------------
async function renderLibrary() {
  crumb.textContent = "";
  const songs = await getJSON("/api/songs");
  const cards = songs.map((s) => {
    const rail = el("div", { className: "rail" },
      s.stages.map((st, i) =>
        el("span", { className: "dot " + (i < s.stage_index ? "done" : i === s.stage_index ? "now" : "") })));
    const badge = s.open_issues
      ? el("span", { className: "badge warn" }, `${s.open_issues} issue(s) to fix`)
      : el("span", { className: "badge" }, STAGE_LABEL[s.stage] || s.stage);
    return el("a", { className: "card", href: `#/song/${encodeURIComponent(s.slug)}` },
      el("h3", {}, s.name), rail, badge);
  });
  app.replaceChildren(
    el("div", { className: "lib" },
      el("div", { className: "lib-head" },
        el("h1", {}, "Songs"),
        el("button", { className: "primary", onclick: newSongDialog }, "+ New song")),
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

async function renderWorkspace(slug) {
  if (ws) { ws.close(); ws = null; }
  const song = await getJSON(`/api/songs/${encodeURIComponent(slug)}`);
  crumb.textContent = "› " + song.name;
  let view = song.stage; // which panel is shown
  let panes = [song.has_pdf ? "pdf" : "original"]; // 1 or 2 docs shown side by side

  const draw = () => {
    const stagebar = el("div", { className: "stagebar" },
      song.stages.map((st, i) => el("div", {
        className: "step " + (st === view ? "active " : "") + (i < song.stage_index ? "done" : ""),
        onclick: () => { view = st; draw(); },
      }, STAGE_LABEL[st] || st)));

    app.replaceChildren(el("div", { className: "ws" }, stagebar, buildPanel(), viewer(song, slug, panes, (p) => { panes = p; draw(); })));
  };

  const buildPanel = () => {
    const panel = el("div", { className: "panel" });
    renderPanel(panel, view, song, slug, () => refresh());
    return panel;
  };

  async function refresh() {
    const fresh = await getJSON(`/api/songs/${encodeURIComponent(slug)}`);
    Object.assign(song, fresh);
    draw();
  }

  draw();

  // live updates: logs + state pings
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(slug)}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "log" && logBox) appendLog(msg.line);
    else if (msg.type === "error" && logBox) appendLog(msg.line, true);
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

function docSrc(slug, doc, fp) {
  const P = `/api/songs/${encodeURIComponent(slug)}`;
  const base = doc === "pdf" ? `${P}/pdf` : `${P}/render?doc=${doc}&v=${encodeURIComponent(fp || "")}`;
  // Collapse the built-in PDF viewer's thumbnail/bookmark sidebar.
  return base + "#navpanes=0&view=FitH";
}

function viewer(song, slug, panes, setPanes) {
  const tabs = viewerTabs(song);
  const keys = tabs.map(([k]) => k);
  // normalize selections to valid docs
  panes = panes.map((p) => (keys.includes(p) ? p : keys[0]));

  const slot = (doc, i) => {
    const setHere = (d) => { const next = panes.slice(); next[i] = d; setPanes(next); };
    const tabRow = tabs.map(([k, label]) => el("button", {
      className: k === doc ? "vtab active" : "vtab", onclick: () => setHere(k),
    }, label));

    const ctrl = panes.length === 1
      ? el("button", { className: "vtab split", title: "Split view",
          onclick: () => setPanes([doc, keys.find((k) => k !== doc) || doc]) }, "⇆ Split")
      : el("button", { className: "vtab close", title: "Close this pane",
          onclick: () => setPanes(panes.filter((_, j) => j !== i)) }, "✕");

    const bar = el("div", { className: "viewtabs" }, ...tabRow, el("span", { className: "spacer" }), ctrl);
    const iframe = el("iframe", { src: docSrc(slug, doc, song.cleaned_fingerprint) });
    return el("div", { className: "vslot" }, bar, iframe);
  };

  return el("div", { className: "viewer" }, panes.map((doc, i) => slot(doc, i)));
}

function appendLog(line, isErr) {
  if (!logBox) return;
  logBox.append(el("div", { className: isErr ? "err" : "" }, line));
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
  if (view === "register") return panelRegister(panel, song);
  if (view === "clean") return panelClean(panel, song, slug, P, refresh);
  if (view === "fix") return panelFix(panel, song, P, refresh);
  if (view === "lyrics") return panelLyrics(panel, song, P, refresh);
  if (view === "review") return panelReview(panel, song, P, refresh);
  if (view === "record") return panelRecord(panel, song, P);
}

function panelRegister(panel, song) {
  panel.append(
    el("h2", {}, "Start"),
    el("p", { className: "sub" }, "Sources for this song."),
    el("p", {}, `Score: ${song.sources?.xml || "—"}`),
    el("p", {}, `PDF: ${song.sources?.pdf || "— (none)"}`),
    el("p", {}, `Mode: ${song.mode}`),
    el("p", { className: "hint" }, "Go to the Clean step to build the score."));
}

async function panelClean(panel, song, slug, P, refresh) {
  panel.append(el("h2", {}, "Clean"),
    el("p", { className: "sub" }, song.mode === "per-system"
      ? "Name each staff's voices per system, then build."
      : "Split the shared-staff voices into one staff per part."));

  const runBtn = el("button", { className: "primary", onclick: async () => {
    runBtn.disabled = true;
    appendLog("Starting clean…");
    await postJSON(`${P}/clean`, {});
  }}, song.has_cleaned ? "Re-clean (discards manual edits)" : "Run clean");

  if (song.mode === "per-system") {
    const holder = el("div", {}, el("p", { className: "hint" }, "Loading systems…"));
    panel.append(holder);
    try {
      const { grid } = await getJSON(`${P}/systems`);
      holder.replaceChildren(...grid.map((sys) => sysBlock(sys)));
      const saveBtn = el("button", { onclick: async () => {
        const answers = {};
        panel.querySelectorAll("input[data-sys]").forEach((inp) => {
          const si = inp.dataset.sys, sid = inp.dataset.staff;
          (answers[si] ||= {})[sid] = inp.value.trim();
        });
        await api(`${P}/systems`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(answers) });
        appendLog("Saved voice assignments.");
      }}, "Save assignments");
      panel.append(el("div", { className: "row" }, saveBtn, runBtn));
    } catch (e) {
      holder.replaceChildren(el("p", { className: "err" }, "Could not read systems: " + e.message));
      panel.append(el("div", { className: "row" }, runBtn));
    }
  } else {
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
        value: st.answer || "", placeholder: st.voices > 1 ? "T1,T2" : "T1",
        "data-sys": sys.system, "data-staff": st.staff_id,
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
  panel.append(el("h2", {}, "Lyrics"),
    el("p", { className: "sub" }, "No API key needed — your AI does the reading, this catches the result."));
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

  const warns = song.lyrics?.warnings || [];
  if (warns.length) {
    panel.append(el("p", { className: "sub" }, "Mismatches (often a note problem — check the measure in MuseScore):"),
      el("ul", { className: "warnlist" }, warns.map((w) => el("li", {}, w))));
  }
  panel.append(makeLog());
}

function panelReview(panel, song, P, refresh) {
  panel.append(el("h2", {}, "Review"),
    el("p", { className: "sub" }, "Final check of notes + lyrics before producing tracks."),
    el("div", { className: "row" },
      el("button", { className: "primary", onclick: () => postJSON(`${P}/open-score`) }, "Open in MuseScore"),
      el("button", { onclick: async () => { await postJSON(`${P}/stage/record`); refresh(); } }, "Looks good → Record")));
}

function panelRecord(panel, song, P) {
  panel.append(el("h2", {}, "Record"),
    el("p", { className: "sub" }, "Export per-voice audio and (optionally) record + upload the play-along video. macOS only."));
  const yt = el("input", { type: "checkbox" });
  const pl = el("input", { placeholder: "YouTube playlist id (optional)" });
  const btn = el("button", { className: "primary", onclick: async () => {
    btn.disabled = true; appendLog("Starting…");
    await postJSON(`${P}/record`, { youtube: yt.checked, playlist: pl.value.trim() || null });
  }}, "Run recording");
  panel.append(
    el("div", { className: "row" }, yt, el("span", {}, "Upload to YouTube")),
    el("label", {}, "Playlist"), pl,
    el("div", { className: "row" }, btn),
    makeLog());
}

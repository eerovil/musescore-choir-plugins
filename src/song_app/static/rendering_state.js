"use strict";

// Workspace state that must survive a browser reload, plus a polling safety net for
// long renders. The main app normally refreshes from its WebSocket; phones can suspend
// or drop that socket while a render runs, so this small companion also watches the
// durable job state. An observed render completion reloads once, and the remembered
// tabs make that reload land exactly where the user was.
(() => {
  const STORAGE_PREFIX = "songWorkspace:";
  const MOBILE_PANES = ["stages", "panel", "viewer"];
  const appRoot = document.getElementById("app");

  let restoreQueued = false;
  let restoring = false;
  let pollTimer = null;
  let observedSlug = null;
  let snapshot = null;

  function currentSlug() {
    const match = location.hash.match(/^#\/song\/(.+)$/);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); }
    catch { return match[1]; }
  }

  function storageKey(slug) {
    return STORAGE_PREFIX + slug;
  }

  function loadWorkspace(slug) {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey(slug)) || "{}");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  function saveWorkspace(patch) {
    const slug = currentSlug();
    if (!slug) return;
    try {
      localStorage.setItem(storageKey(slug), JSON.stringify({ ...loadWorkspace(slug), ...patch }));
    } catch {
      // Storage can be disabled in private browsing. The app still works; only the
      // convenience of returning to the same tab is unavailable in that browser.
    }
  }

  function clickForRestore(element) {
    restoring = true;
    try { element.click(); }
    finally { restoring = false; }
  }

  // Remember deliberate navigation. Capture phase records it before app.js redraws
  // the panel and replaces the clicked node. A stage choice deliberately brings the
  // panel forward on phones, so that resulting pane — not the old Stages pane — is
  // what should survive the next reload.
  document.addEventListener("click", (event) => {
    if (restoring) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const stage = target.closest(".stagebar .step");
    if (stage) saveWorkspace({ stage: stage.textContent.trim(), mobilePane: "panel" });

    const mobile = target.closest(".mobilebar .mtab");
    if (mobile) {
      const buttons = [...mobile.closest(".mobilebar").querySelectorAll(".mtab")];
      const index = buttons.indexOf(mobile);
      if (index >= 0) saveWorkspace({ mobilePane: MOBILE_PANES[index] });
    }

    const viewerTab = target.closest(".viewtabs .vtab:not(.split):not(.close)");
    if (viewerTab) {
      const slot = viewerTab.closest(".vslot");
      const slots = [...document.querySelectorAll(".viewer .vslot")];
      const index = slots.indexOf(slot);
      if (index >= 0) {
        const saved = loadWorkspace(currentSlug());
        const viewerTabs = Array.isArray(saved.viewerTabs) ? [...saved.viewerTabs] : [];
        viewerTabs[index] = viewerTab.textContent.trim();
        saveWorkspace({ viewerTabs });
      }
    }
  }, true);

  function queueRestore() {
    if (restoreQueued) return;
    restoreQueued = true;
    requestAnimationFrame(() => {
      restoreQueued = false;
      restoreWorkspace();
    });
  }

  function restoreWorkspace() {
    const slug = currentSlug();
    if (!slug) return;
    const saved = loadWorkspace(slug);

    // Restore the workflow stage first. Its click redraws the panel, so restore the
    // mobile/viewer tabs on the next pass. Synthetic restore clicks are not allowed
    // to overwrite what the person actually chose before reloading.
    if (saved.stage) {
      const steps = [...document.querySelectorAll(".stagebar .step")];
      const wanted = steps.find((step) => step.textContent.trim() === saved.stage);
      if (wanted && !wanted.classList.contains("active")) {
        clickForRestore(wanted);
        queueRestore();
        return;
      }
    }

    if (Array.isArray(saved.viewerTabs)) {
      const slots = [...document.querySelectorAll(".viewer .vslot")];
      saved.viewerTabs.forEach((label, index) => {
        if (!label || !slots[index]) return;
        const tabs = [...slots[index].querySelectorAll(".viewtabs .vtab:not(.split):not(.close)")];
        const wanted = tabs.find((tab) => tab.textContent.trim() === label);
        if (wanted && !wanted.classList.contains("active")) clickForRestore(wanted);
      });
    }

    const paneIndex = MOBILE_PANES.indexOf(saved.mobilePane);
    const mobileButtons = [...document.querySelectorAll(".mobilebar .mtab")];
    if (paneIndex >= 0 && mobileButtons[paneIndex]
        && !mobileButtons[paneIndex].classList.contains("active")) {
      clickForRestore(mobileButtons[paneIndex]);
    }
  }

  const observer = new MutationObserver(queueRestore);
  if (appRoot) observer.observe(appRoot, { childList: true, subtree: true });

  function renderedMedia(data) {
    const media = Array.isArray(data.media) ? data.media : [];
    const merged = media.filter((item) => item.merged);
    return merged.length ? merged : media;
  }

  function completionToken(data) {
    const job = data.jobs?.render || {};
    return job.finished_at || data.record?.rendered_against || "";
  }

  function mediaUrl(url, token) {
    const parsed = new URL(url, location.href);
    // The server already versions media by mtime and size. The durable job token is
    // an extra guarantee for a same-name, same-size render completed within one mtime
    // tick, and lets an already-mounted <video> unequivocally load the new take.
    if (token) parsed.searchParams.set("completed", String(token));
    return parsed.pathname + parsed.search;
  }

  function mediaSignature(data) {
    const token = completionToken(data);
    return renderedMedia(data).map((item) => `${item.label}:${mediaUrl(item.url, token)}`).join("|");
  }

  function syncProgress(data) {
    const logs = data.jobs?.render?.logs || [];
    const latest = [...logs].reverse().find((entry) => entry.type === "progress");
    if (!latest) return;
    const log = document.querySelector(".panel .log");
    if (!log) return;
    const rows = log.querySelectorAll(".progress");
    let row = rows[rows.length - 1];
    if (!row) {
      row = document.createElement("div");
      row.className = "progress";
      log.append(row);
    }
    row.textContent = latest.line;
    log.scrollTop = log.scrollHeight;
  }

  function refreshVideosInPlace(data) {
    const expected = renderedMedia(data);
    if (!expected.length) return true;
    const rows = [...document.querySelectorAll(".panel .result")];
    if (rows.length !== expected.length) return false;

    for (let index = 0; index < expected.length; index += 1) {
      const label = rows[index].querySelector(".rlabel")?.textContent.trim();
      const video = rows[index].querySelector("video");
      if (!video || label !== expected[index].label) return false;
    }

    const token = completionToken(data);
    rows.forEach((row, index) => {
      const video = row.querySelector("video");
      const wanted = mediaUrl(expected[index].url, token);
      const shown = video.getAttribute("src") || "";
      if (shown !== wanted) {
        video.src = wanted;
        video.load();
      }
    });
    return true;
  }

  function renderRunning(data) {
    return !!data.recording || data.jobs?.render?.status === "running";
  }

  function schedulePoll(delay) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, delay);
  }

  async function poll() {
    pollTimer = null;
    const slug = currentSlug();
    if (!slug) {
      observedSlug = null;
      snapshot = null;
      schedulePoll(3000);
      return;
    }
    if (slug !== observedSlug) {
      observedSlug = slug;
      snapshot = null;
      queueRestore();
    }

    let delay = 2500;
    try {
      const response = await fetch(`/api/songs/${encodeURIComponent(slug)}`, { cache: "no-store" });
      if (!response.ok) throw new Error(response.statusText);
      const data = await response.json();
      syncProgress(data);

      const current = {
        running: renderRunning(data),
        signature: mediaSignature(data),
      };
      const completed = snapshot?.running && !current.running;
      const outputsChanged = !!snapshot && snapshot.signature !== current.signature
        && renderedMedia(data).length > 0;
      const updated = refreshVideosInPlace(data);

      // A complete refresh clears the old running banner, re-enables the controls,
      // and mounts the final output set. It happens once because the next page starts
      // with an idle snapshot; the saved workspace state restores the exact tab/pane.
      if (completed) {
        location.reload();
        return;
      }
      // Also recover if outputs changed without this page ever observing the running
      // state (for example, a phone resumed after the whole render) and its old panel
      // cannot be updated safely in place.
      if (outputsChanged && !updated) {
        location.reload();
        return;
      }

      snapshot = current;
      delay = current.running ? 1000 : 4000;
    } catch {
      // A transient request failure must not affect the render. Keep trying quietly.
      delay = 2000;
    }
    schedulePoll(delay);
  }

  window.addEventListener("hashchange", () => {
    observedSlug = null;
    snapshot = null;
    queueRestore();
    schedulePoll(0);
  });
  window.addEventListener("DOMContentLoaded", () => {
    queueRestore();
    schedulePoll(0);
  });
})();

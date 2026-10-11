"use strict";

// Workspace state that must survive a browser reload: the stage, the viewer tabs and
// the phone's pane, so a reload lands exactly where the user was.
(() => {
  const STORAGE_PREFIX = "songWorkspace:";
  // The phone bar's tabs, in order. Stages left it for a drawer (#258); a "stages"
  // remembered from before then simply finds no tab and the panel stays forward.
  const MOBILE_PANES = ["panel", "viewer"];
  const appRoot = document.getElementById("app");

  let restoreQueued = false;
  let restoring = false;

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

  window.addEventListener("hashchange", queueRestore);
  window.addEventListener("DOMContentLoaded", queueRestore);
})();

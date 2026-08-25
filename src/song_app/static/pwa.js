"use strict";

/*
 * Register the root-scoped service worker and pick up new frontend generations.
 * The first controller claim is not an update and must not reload the page. A
 * later controller change is a deploy; reload only after the user is no longer
 * editing a form, so an update can never throw away unsaved work.
 */
(() => {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;

  const FIELD_SELECTOR = "input, textarea, select, [contenteditable='true']";
  const settled = new WeakMap();
  let snapshotQueued = false;
  let updatePending = false;
  let reloading = false;
  let retryTimer = null;
  let controlled = navigator.serviceWorker.controller !== null;

  function fieldValue(field) {
    if (field.matches("input[type='checkbox'], input[type='radio']")) {
      return field.checked ? "checked" : "unchecked";
    }
    if (field.matches("input[type='file']")) {
      return field.files && field.files.length
        ? [...field.files].map((file) => `${file.name}:${file.size}`).join("|")
        : "";
    }
    if (field.isContentEditable) return field.textContent || "";
    return field.value;
  }

  function snapshotNewFields() {
    document.querySelectorAll(FIELD_SELECTOR).forEach((field) => {
      if (!settled.has(field)) settled.set(field, fieldValue(field));
    });
  }

  function scheduleSnapshot() {
    if (snapshotQueued) return;
    snapshotQueued = true;
    setTimeout(() => {
      snapshotQueued = false;
      snapshotNewFields();
    }, 0);
  }

  function hasChangedField() {
    return [...document.querySelectorAll(FIELD_SELECTOR)].some((field) => {
      if (!settled.has(field)) return false;
      return fieldValue(field) !== settled.get(field);
    });
  }

  function activelyEditing() {
    const active = document.activeElement;
    const focusedField = active && (
      active.isContentEditable
      || (active.matches && active.matches("input, textarea, select"))
    );
    return Boolean(focusedField) || hasChangedField();
  }

  function reloadWhenIdle() {
    if (reloading || !updatePending || activelyEditing()) return;
    reloading = true;
    if (retryTimer !== null) clearInterval(retryTimer);
    location.reload();
  }

  const observer = new MutationObserver(scheduleSnapshot);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("DOMContentLoaded", scheduleSnapshot);
  window.addEventListener("pageshow", scheduleSnapshot);

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    const wasControlled = controlled;
    controlled = navigator.serviceWorker.controller !== null;
    if (!wasControlled) return;

    updatePending = true;
    reloadWhenIdle();
    if (!reloading && retryTimer === null) {
      retryTimer = setInterval(reloadWhenIdle, 3000);
    }
  });

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", {
      scope: "/",
      updateViaCache: "none",
    }).then((registration) => registration.update()).catch(() => {
      // PWA support is an enhancement. Plain HTTP remote origins are not secure
      // contexts, and a registration failure must never stop the song app.
    });
  });
})();

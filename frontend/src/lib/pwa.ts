// Registers the PWA service worker (offline app-shell caching, installability).
// Push notifications require a subscribed endpoint; delivery itself only
// fires from the owner's scheduler today (see PLANNING.md P6.5) — the SW's
// push/notificationclick handlers are wired and ready for when that lands.
export function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("Service worker registration failed", err);
    });
  });
}

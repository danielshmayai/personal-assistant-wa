import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { registerServiceWorker } from "./lib/pwa";

registerServiceWorker();

// iOS Safari never resizes the layout viewport when the keyboard opens — it
// pans the page instead, hiding the chat composer behind the keyboard. Track
// the visual viewport and pin the app's height to it (consumed by #root via
// --app-height in index.css). Android/desktop resize the layout viewport
// natively, so this is effectively a no-op there.
function trackAppHeight() {
  const vv = window.visualViewport;
  if (!vv) return;
  const apply = () => {
    document.documentElement.style.setProperty("--app-height", `${Math.round(vv.height)}px`);
    // Counter iOS's automatic pan-on-focus so the layout stays anchored.
    window.scrollTo(0, 0);
  };
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  apply();
}
trackAppHeight();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

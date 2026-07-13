import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// sw.js's cache-busting VERSION used to be a hand-edited constant — every
// deploy that forgot to bump it left phones stuck on a stale cached shell
// (see the mobile "always desktop mode" bug this session). Stamp it with
// this build's timestamp automatically so it can never be forgotten again.
function stampServiceWorkerVersion(): Plugin {
  return {
    name: "stamp-sw-version",
    closeBundle() {
      const swPath = resolve(__dirname, "dist/sw.js");
      const version = new Date().toISOString();
      const contents = readFileSync(swPath, "utf-8").replace(
        /const VERSION = '.*';/,
        `const VERSION = '${version}';`,
      );
      writeFileSync(swPath, contents);
    },
  };
}

// Dev proxy → product API (run: uvicorn product.main:app --port 8000)
export default defineConfig({
  plugins: [react(), tailwindcss(), stampServiceWorkerVersion()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
// Engine origin the dev server proxies API calls to. Overridable so a second
// dev server can be pointed at another engine instance (e.g. a source build on
// :8011) without fighting over :8001.
// @ts-expect-error process is a nodejs global
const ENGINE_URL = process.env.JAMBU_ENGINE_URL || "http://localhost:8001";

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), tailwindcss()],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
    // Proxy API calls to the Python engine (JAMBU_ENGINE_URL, :8001 by default)
    proxy: Object.fromEntries(
      [
        "/v2", "/v1", "/research", "/search", "/scrape", "/act", "/login",
        "/exec", "/health", "/stats", "/privacy", "/audit", "/vault",
        "/security", "/fingerprint", "/knowledge", "/mission", "/consensus",
        "/vision", "/computer", "/multimodal", "/mlx", "/goal", "/p2p",
        "/tools", "/sessions",
        // The engine's browser-session API: agent sessions, AX tree, act-by-ref,
        // frames, event stream and memory sync — plus the human-takeover call
        // that predated all of it. Without this entry the Agent Browser pane
        // (and the takeover button) get the SPA fallback instead of the engine.
        "/browser",
      ].map((route) => [route, ENGINE_URL]),
    ),
  },

  // Vendor / panel chunk splitting. Without this, every dependency — including
  // heavy ones like react-force-graph-2d (force-directed graph layout) and
  // framer-motion — get bundled into a single 634 KB index.js. Splitting them
  // into named chunks:
  //   1. Cuts the initial bundle so the desktop shell loads faster
  //   2. Lets the browser cache each vendor chunk independently (only re-fetch
  //      what actually changed across deploys)
  //   3. Surfaces which dependencies are the heavy hitters via the build report
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (!id.includes("node_modules")) return undefined;
          // The order matters: check specific patterns first so the catch-all
          // "vendor" chunk only contains things we haven't categorized.
          if (id.includes("react-force-graph") || id.includes("d3-force") || id.includes("three") || id.includes("d3-")) {
            return "vendor-force-graph";
          }
          if (id.includes("framer-motion") || id.includes("/motion/")) {
            return "vendor-motion";
          }
          if (id.includes("@radix-ui")) {
            return "vendor-radix";
          }
          if (id.includes("tailwind-merge") || id.includes("class-variance-authority") || id.includes("clsx")) {
            return "vendor-tw-utils";
          }
          if (id.includes("@tauri-apps")) {
            return "vendor-tauri";
          }
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) {
            return "vendor-react";
          }
          if (id.includes("zustand") || id.includes("cmdk") || id.includes("lucide-react") || id.includes("tw-animate-css")) {
            return "vendor-misc";
          }
          return "vendor";
        },
      },
    },
    // Bump the warning threshold so we don't get a noisy 500 KB warning on
    // the force-graph chunk specifically (it's a known heavy dep, lazy-loaded
    // by the Knowledge panel).
    chunkSizeWarningLimit: 700,
  },
}));

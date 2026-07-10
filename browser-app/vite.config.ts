import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

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
    // Proxy API calls to the Python backend on port 8001
    proxy: {
      "/v2": "http://localhost:8001",
      "/v1": "http://localhost:8001",
      "/research": "http://localhost:8001",
      "/search": "http://localhost:8001",
      "/scrape": "http://localhost:8001",
      "/act": "http://localhost:8001",
      "/login": "http://localhost:8001",
      "/exec": "http://localhost:8001",
      "/health": "http://localhost:8001",
      "/stats": "http://localhost:8001",
      "/privacy": "http://localhost:8001",
      "/audit": "http://localhost:8001",
      "/vault": "http://localhost:8001",
      "/security": "http://localhost:8001",
      "/fingerprint": "http://localhost:8001",
      "/knowledge": "http://localhost:8001",
      "/mission": "http://localhost:8001",
      "/consensus": "http://localhost:8001",
      "/vision": "http://localhost:8001",
      "/computer": "http://localhost:8001",
      "/multimodal": "http://localhost:8001",
      "/mlx": "http://localhost:8001",
      "/goal": "http://localhost:8001",
      "/p2p": "http://localhost:8001",
      "/tools": "http://localhost:8001",
    },
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

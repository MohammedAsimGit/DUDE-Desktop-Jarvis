import { defineConfig } from "vite";

// Tauri expects a fixed dev-server port; the Rust host talks to this server
// during `npm run tauri dev`.
export default defineConfig({
  plugins: [],
  // Vite env variables starting with VITE_ are exposed to the client. None are
  // used in Phase 0; do not put secrets in VITE_* variables ever.
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "chrome105",
    minify: "esbuild",
    sourcemap: false,
  },
});

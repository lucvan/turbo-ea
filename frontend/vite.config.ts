import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

const versionFile = [
  path.resolve(__dirname, "../VERSION"),
  path.resolve(__dirname, "VERSION"),
].find((f) => fs.existsSync(f));
const upstreamVersion = versionFile
  ? fs.readFileSync(versionFile, "utf-8").trim()
  : "0.0.0-dev";
// This fork's own revision, appended as a fourth component exactly as
// `backend/app/config.py` does — the two must agree, or `appUpdateGuard`
// reloads the page on every load.
const forkFile = versionFile && path.join(path.dirname(versionFile), "FORK_VERSION");
const forkRevision =
  forkFile && fs.existsSync(forkFile) ? fs.readFileSync(forkFile, "utf-8").trim() : "";
const version = /^\d+$/.test(forkRevision)
  ? `${upstreamVersion}.${forkRevision}`
  : upstreamVersion;

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});

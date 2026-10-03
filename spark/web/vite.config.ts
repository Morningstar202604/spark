import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(here, "src"),
    },
  },
  build: {
    outDir: path.resolve(here, "dist"),
    emptyOutDir: true,
  },
  server: {
    allowedHosts: [".monkeycode-ai.online"],
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": {
        target: "http://127.0.0.1:8000",
        ws: true,
      },
    },
  },
});

/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the local FastAPI backend so the browser only talks to one origin.
const backendPort = process.env.CHOTU_PORT ?? "8765";

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": `http://127.0.0.1:${backendPort}` } },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});

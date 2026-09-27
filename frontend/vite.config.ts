/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The SPA calls the API same-origin under /api; the dev and preview servers proxy it
// (the API sends no CORS headers and never uses cookies, API_PLAN §1.2).
const api = process.env.OG_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy: { "/api": api } },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy: { "/api": api } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});

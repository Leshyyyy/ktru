import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_MODE:
//   memory (по умолчанию) — встроенная имитация бэкенда в браузере (src/mock), ничего поднимать не нужно;
//   http — настоящие запросы на /api/v1: в dev их принимает Prism (npm run dev:prism) или бэкенд на :8000.
const backend = process.env.API_PROXY_TARGET ?? "http://localhost:4010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/v1": {
        target: backend,
        changeOrigin: true,
        // Prism отдаёт пути без префикса из servers.url, бэкенд — с префиксом.
        rewrite: (p) => (backend.includes(":4010") ? p.replace(/^\/api\/v1/, "") : p),
      },
    },
  },
});

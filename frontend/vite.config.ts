import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Режим запуска выбирается файлом .env.<mode> (так работает одинаково на Windows, macOS и Linux):
//   npm run dev          → .env          VITE_API_MODE=memory, бэкенд имитируется в браузере (src/mock)
//   npm run dev:prism    → .env.prism    запросы на /api/v1 уходят в Prism (:4010)
//   npm run dev:backend  → .env.backend  запросы на /api/v1 уходят в бэкенд (:8000)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.API_PROXY_TARGET || "http://localhost:4010";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api/v1": {
          target: backend,
          changeOrigin: true,
          // Prism отдаёт пути без префикса из servers.url, бэкенд — с префиксом.
          rewrite: (p: string) => (backend.includes(":4010") ? p.replace(/^\/api\/v1/, "") : p),
        },
      },
    },
  };
});

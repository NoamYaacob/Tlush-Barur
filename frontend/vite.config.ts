import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],

  // Ensure the dev server binds to 127.0.0.1 as required
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // Proxy /api requests to the FastAPI backend
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
      },
      // Proxy /health requests to the FastAPI backend
      "/health": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
      },
    },
  },
});

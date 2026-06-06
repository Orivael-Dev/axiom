import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server on a fixed port so Tauri's devUrl can point at it.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
});

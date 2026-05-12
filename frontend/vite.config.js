import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 5173
  },
  build: {
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ["vue"],
          antd: ["ant-design-vue", "@ant-design/icons-vue"],
          charts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"]
        }
      }
    }
  }
});

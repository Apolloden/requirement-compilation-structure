import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  workers: Number(process.env.ARCFLASH_WORKERS || 4),
  retries: 0,
  timeout: 30000,
  expect: { timeout: 5000 },
  reporter: [["json", { outputFile: "results.json" }]],
  use: {
    baseURL: process.env.ARCFLASH_BASE_URL,
    headless: true,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});

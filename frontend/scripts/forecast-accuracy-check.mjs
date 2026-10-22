// Real UI/API regression with a disposable synthetic assessment history.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "../..");
const socket = net.createServer();
await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
const api = spawn(
  path.join(root, "backend/.venv/bin/python"),
  [
    "-m",
    "uvicorn",
    "tests.forecast_accuracy_browser_app:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--log-level",
    "warning",
  ],
  { cwd: path.join(root, "backend"), stdio: ["ignore", "pipe", "pipe"] },
);
let vite,
  browser,
  stderr = "";
api.stderr.on("data", (chunk) => {
  stderr += chunk;
});
try {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) break;
    } catch {
      /* starting */
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  vite = await createServer({
    root: path.join(root, "frontend"),
    configFile: path.join(root, "frontend/vite.config.ts"),
    server: {
      host: "127.0.0.1",
      port: 0,
      proxy: { "/api": `http://127.0.0.1:${port}` },
    },
  });
  await vite.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}/`, {
    waitUntil: "networkidle",
  });
  const panel = page.getByRole("region", {
    name: "Forecast Accuracy",
    exact: true,
  });
  await panel
    .getByRole("heading", { name: "Forecast Accuracy", exact: true })
    .waitFor();
  await panel.getByText("MAE 10", { exact: false }).waitFor();
  assert.equal(await panel.locator("form").count(), 0);
  await panel
    .getByText("Later corrections / evidence changes (1)", { exact: true })
    .click();
  await panel.getByText("actual 150", { exact: false }).waitFor();
  await panel
    .getByText("Primary score remains fixed.", { exact: false })
    .waitFor();
  await panel
    .getByText("Snapshot and actual sources", { exact: true })
    .last()
    .click();
  assert.ok(
    (await panel
      .getByRole("link", { name: /F1 .*Result source|F1 .*REVENUE/ })
      .count()) > 0,
  );
  assert.ok(
    (await panel.getByText("Insufficient data", { exact: true }).count()) > 0,
  );
  await panel
    .getByText("Rolling accuracy and calibration history (last 20 forecasts)", {
      exact: true,
    })
    .click();
  assert.ok(
    await panel
      .getByRole("columnheader", { name: "Evaluation IDs", exact: true })
      .isVisible(),
  );
  await panel
    .getByLabel("Forecast metric", { exact: true })
    .selectOption("PAT");
  await panel
    .getByText("Insufficient data: no eligible evaluated Almonds forecasts.", {
      exact: true,
    })
    .waitFor();
  assert.ok(
    await panel
      .getByRole("cell")
      .filter({ hasText: "metric actual missing" })
      .isVisible(),
  );
  await panel
    .getByLabel("Forecast metric", { exact: true })
    .selectOption("DILUTED_EPS");
  await panel
    .getByText("No forecasts match these filters.", { exact: true })
    .waitFor();
  await panel.getByLabel("Forecast metric", { exact: true }).selectOption("");
  await panel.getByText("MAE 10", { exact: false }).waitFor();
  await panel
    .getByRole("button", { name: "Evaluate available results", exact: true })
    .click();
  await panel.getByText("Scoring queued.", { exact: false }).waitFor();
  await page.route(
    "**/api/companies/ACCURACY/forecasts/comparisons?**",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Test comparison failure" }),
      }),
  );
  await panel
    .getByLabel("Forecast period type", { exact: true })
    .selectOption("QUARTERLY");
  await panel
    .getByRole("alert")
    .getByText(/Test comparison failure/)
    .waitFor();
  assert.ok(await panel.getByText("MAE 10", { exact: false }).isVisible());
  await page.unroute("**/api/companies/ACCURACY/forecasts/comparisons?**");
  await panel
    .getByRole("button", { name: "Retry forecast accuracy", exact: true })
    .click();
  await panel.getByRole("alert").waitFor({ state: "hidden" });
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(
    "**/api/companies/ACCURACY/forecasts/accuracy?**",
    async (route) => {
      await gate;
      await route.continue();
    },
  );
  await panel.getByLabel("Forecast horizon", { exact: true }).selectOption("1");
  await panel
    .getByRole("status")
    .filter({ hasText: "Loading forecast accuracy" })
    .waitFor();
  release();
  await panel
    .getByText("Insufficient data: no eligible evaluated Almonds forecasts.", {
      exact: true,
    })
    .waitFor();
  await page.unroute("**/api/companies/ACCURACY/forecasts/accuracy?**");
  await panel.getByLabel("Forecast horizon", { exact: true }).selectOption("");
  await panel.getByText("MAE 10", { exact: false }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "mobile overflow",
  );
  await panel.screenshot({ path: "/tmp/almonds-phase83b-mobile.png" });
  assert.deepEqual(errors, []);
  console.log(
    "PASS: Phase 8.3B primary vs corrected actuals, calibration uncertainty, sources, filters, scoring queue, loading/empty/partial error/retry, rolling history, mobile layout",
  );
} catch (error) {
  console.error(stderr);
  for (const context of browser?.contexts() || [])
    for (const page of context.pages())
      console.error(
        await page
          .locator(".accuracy-panel")
          .innerText({ timeout: 2000 })
          .catch(() => "Accuracy not visible"),
      );
  throw error;
} finally {
  await browser?.close();
  await vite?.close();
  api.kill("SIGTERM");
}

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
    "tests.accuracy_browser_app:app",
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
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}/`, {
    waitUntil: "networkidle",
  });
  const panel = page.getByRole("region", {
    name: "Historical Accuracy",
    exact: true,
  });
  await panel
    .getByRole("heading", { name: "Expectation vs actual", exact: true })
    .waitFor();
  const automatic = panel.locator('[aria-label="Autonomous research"]');
  await automatic
    .getByRole("heading", { name: "Upcoming forecasts", exact: true })
    .waitFor();
  await automatic
    .getByText("No eligible future forecasts yet.", { exact: false })
    .waitFor();
  assert.ok(
    await automatic
      .getByText("No original Almonds forecasts have been captured yet.", {
        exact: true,
      })
      .isVisible(),
  );
  assert.ok(
    await automatic
      .getByRole("heading", { name: "Valuation intelligence", exact: true })
      .isVisible(),
  );
  assert.ok(
    await automatic
      .getByRole("heading", { name: "Research automation status", exact: true })
      .isVisible(),
  );
  assert.equal(await automatic.locator("form").count(), 0);
  // The autonomous panels retain partial results, report failures, and recover.
  await page.route(
    "**/api/companies/ACCURACY/valuation/automated?**",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "test unavailable" }),
      }),
  );
  await page.route("**/api/companies/ACCURACY/forecasts?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });
  await page.reload();
  await automatic
    .getByRole("status")
    .filter({ hasText: "Loading automatic research" })
    .waitFor();
  await automatic
    .getByRole("alert")
    .filter({ hasText: "Some research panels are unavailable" })
    .waitFor();
  assert.ok(
    await automatic
      .getByRole("heading", { name: "Upcoming forecasts", exact: true })
      .isVisible(),
  );
  await page.unroute("**/api/companies/ACCURACY/valuation/automated?**");
  await page.unroute("**/api/companies/ACCURACY/forecasts?**");
  await automatic
    .getByRole("button", { name: "Retry automatic research", exact: true })
    .click();
  await automatic.getByRole("alert").waitFor({ state: "hidden" });
  assert.ok(
    await panel
      .getByText("Evidence coverage: 3 / 3", { exact: false })
      .isVisible(),
  );
  assert.equal(
    await panel.getByRole("img", { name: /Reliability chart/ }).count(),
    1,
  );
  assert.ok(
    await panel.getByText("Brier score: —", { exact: false }).isVisible(),
  );
  assert.equal(
    await panel.getByRole("heading", { name: /Management checkpoint/ }).count(),
    5,
  );
  assert.equal(
    await panel
      .getByRole("heading", { name: /Management checkpoint · pending/ })
      .count(),
    3,
  );
  assert.ok(
    await panel
      .getByText(
        "No explicit deadline; no reporting period has been inferred.",
        { exact: true },
      )
      .isVisible(),
  );
  assert.ok(
    await panel
      .getByText(/Ambiguous target: total versus additional capex/)
      .isVisible(),
  );
  assert.equal(
    await panel.getByRole("link", { name: /Original source · page/ }).count(),
    5,
  );
  assert.equal(await panel.getByText(/Possible overlap with K/).count(), 2);
  await panel.getByText("Revision history", { exact: true }).first().click();
  await panel.getByText("Version 1", { exact: false }).first().waitFor();
  assert.match(
    await panel
      .getByRole("link", { name: /F1 · page 3/ })
      .first()
      .getAttribute("href"),
    /\/api\/documents\/\d+\/file#page=3/,
  );
  await panel.getByLabel("Source filter", { exact: true }).selectOption("AI");
  await panel
    .getByText("No expectations match these filters.", { exact: false })
    .waitFor();
  await panel.getByLabel("Source filter", { exact: true }).selectOption("");
  await panel
    .getByRole("heading", { name: "Accuracy by metric", exact: true })
    .waitFor();
  // Delay a real API request to assert loading without adding production fixtures.
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(
    "**/api/companies/ACCURACY/expectations?**",
    async (route) => {
      await gate;
      await route.continue();
    },
  );
  await panel.getByLabel("Period end", { exact: true }).fill("2026-03-31");
  await panel
    .getByText("Loading historical accuracy…", { exact: true })
    .waitFor();
  release();
  await panel
    .getByText("No expectations match these filters.", { exact: false })
    .waitFor();
  await page.unroute("**/api/companies/ACCURACY/expectations?**");
  await page.route("**/api/companies/ACCURACY/accuracy?**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Temporary accuracy test failure" }),
    }),
  );
  await panel.getByLabel("Period end", { exact: true }).fill("");
  await panel
    .getByRole("alert")
    .getByText("Temporary accuracy test failure")
    .waitFor();
  await page.unroute("**/api/companies/ACCURACY/accuracy?**");
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  await panel
    .getByRole("heading", { name: "Accuracy by metric", exact: true })
    .waitFor();
  await panel
    .getByText("Advanced: register a manual expectation", { exact: true })
    .click();
  const form = panel.locator("form");
  await form
    .getByLabel("Target period end", { exact: true })
    .fill("2027-06-30");
  await form
    .getByLabel("Expected value / range minimum", { exact: true })
    .fill("120");
  await form
    .getByLabel("Cutoff (local time)", { exact: true })
    .fill("2027-07-15T00:00");
  await form
    .getByLabel("Source reference", { exact: true })
    .fill("Browser registration");
  await form
    .getByLabel("Original expectation", { exact: true })
    .fill("Revenue expected at 120 crore");
  await form
    .getByRole("button", { name: "Save expectation", exact: true })
    .click();
  await form
    .getByText("Expectation saved with its capture time and provenance.", {
      exact: true,
    })
    .waitFor();
  const stored = await (
    await fetch(`http://127.0.0.1:${port}/api/companies/ACCURACY/expectations`)
  ).json();
  assert.equal(stored.total, 4);
  assert.equal(stored.items[0].status, "PENDING");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "mobile overflow",
  );
  await panel.screenshot({ path: "/tmp/almonds-phase83-mobile.png" });
  assert.deepEqual(errors, []);
  console.log(
    "PASS: persisted outcomes, source links, revision history, probabilistic chart, sample guards, filters, loading/empty/error/retry, registration, mobile layout",
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

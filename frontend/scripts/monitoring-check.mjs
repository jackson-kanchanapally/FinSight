// Real UI/API regression with a disposable synthetic assessment history.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = path.resolve(import.meta.dirname, "../..");
const socket = net.createServer();
await new Promise(resolve => socket.listen(0, "127.0.0.1", resolve));
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const api = spawn(path.join(root, "backend/.venv/bin/python"),
  ["-m", "uvicorn", "tests.monitoring_browser_app:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
  { cwd: path.join(root, "backend"), stdio: ["ignore", "pipe", "pipe"] });
let vite, browser, stderr = "";
api.stderr.on("data", chunk => { stderr += chunk; });
try {
  for (let attempt = 0; attempt < 60; attempt++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) break; }
    catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const before = await (await fetch(`http://127.0.0.1:${port}/api/companies/MONITOR/thesis`)).json();
  assert.equal(before.monitoring.history.length, 2);
  vite = await createServer({ root: path.join(root, "frontend"),
    configFile: path.join(root, "frontend/vite.config.ts"),
    server: { host: "127.0.0.1", port: 0, proxy: { "/api": `http://127.0.0.1:${port}` } } });
  await vite.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}/`, { waitUntil: "networkidle" });
  const panel = page.getByRole("region", { name: "Investment Thesis", exact: true });
  const monitoring = panel.getByRole("region", { name: "Thesis monitoring", exact: true });
  await monitoring.getByText("Evidence confidence:", { exact: false }).waitFor();
  await monitoring.getByText("Last evaluated", { exact: false }).waitFor();
  assert.ok(await monitoring.getByText("Deteriorating factors", { exact: false }).isVisible());
  await monitoring.getByText("Assessment timeline", { exact: false }).click();
  await monitoring.getByLabel("Assessment", { exact: true }).selectOption("1");
  await monitoring.getByText("Initial baseline", { exact: true }).waitFor();
  assert.ok(await monitoring.getByText("Improving factors", { exact: false }).isVisible());
  await monitoring.getByLabel("Assessment", { exact: true }).selectOption("2");
  await monitoring.getByLabel("Compare with", { exact: true }).selectOption("1");
  await monitoring.getByText("Comparing assessment #1 with #2.", { exact: true }).waitFor();
  await monitoring.locator(".thesis-monitor-deteriorating .thesis-evidence details summary").first().click();
  const source = monitoring.getByRole("link", { name: "Open source · page 3", exact: true }).first();
  assert.match(await source.getAttribute("href"), /\/api\/documents\/\d+\/file#page=3/);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "mobile overflow");
  await panel.screenshot({ path: "/tmp/almonds-phase82-monitoring-mobile.png" });
  await panel.locator(".thesis-date-control > summary").click();
  await panel.getByLabel("History as of").fill("2026-07-01");
  await panel.getByText("Current Investment Case · 2026-07-01").waitFor();
  assert.equal(await panel.getByRole("region", { name: "Thesis monitoring", exact: true }).count(), 0);
  const after = await (await fetch(`http://127.0.0.1:${port}/api/companies/MONITOR/thesis`)).json();
  assert.equal(after.monitoring.history.length, before.monitoring.history.length);
  assert.deepEqual(errors, []);
  console.log("PASS: current assessment, material factors, historical timeline, arbitrary comparison, source page links, historical cutoff, idempotent reads, mobile layout");
} catch (error) {
  console.error(stderr);
  for (const context of browser?.contexts() || [])
    for (const page of context.pages()) console.error(await page.locator(".thesis-panel").innerText({ timeout: 2000 }).catch(() => "Thesis not visible"));
  throw error;
} finally {
  await browser?.close();
  await vite?.close();
  api.kill("SIGTERM");
}

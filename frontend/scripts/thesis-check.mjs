// Browser regression against a disposable local SQLite API.
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
  ["-m", "uvicorn", "tests.thesis_browser_app:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
  { cwd: path.join(root, "backend"), stdio: ["ignore", "pipe", "pipe"] });
let vite, browser, stderr = "";
api.stderr.on("data", chunk => { stderr += chunk; });
try {
  for (let attempt = 0; attempt < 60; attempt++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) break; }
    catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const created = await fetch(`http://127.0.0.1:${port}/api/companies`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol: "GENERIC", name: "Generic Limited", exchange: "NSE" }),
  });
  assert.ok(created.ok, await created.text());
  vite = await createServer({ root: path.join(root, "frontend"),
    configFile: path.join(root, "frontend/vite.config.ts"),
    server: { host: "127.0.0.1", port: 0, proxy: { "/api": `http://127.0.0.1:${port}` } } });
  await vite.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}/`, { waitUntil: "networkidle" });
  const panel = page.getByRole("region", { name: "Investment Thesis" });
  await panel.getByText("A thesis appears when verified quarterly research has enough evidence.").waitFor();
  assert.equal(await panel.getByText("No thesis evaluation recorded.").isVisible(), false);
  assert.equal(await panel.getByText("Thesis History · 0 records").count(), 0);
  assert.equal(await panel.getByRole("button", { name: "Evaluate thesis" }).count(), 0);
  await panel.locator(".thesis-editor > summary").click();
  await panel.getByRole("button", { name: "Add pillar" }).click();
  await panel.getByLabel("Name", { exact: true }).fill("Durable profitability");
  await panel.getByLabel("Investment requirement", { exact: true }).fill("Profitability must remain consistent with the investment requirement.");
  await panel.getByLabel("Importance", { exact: true }).selectOption("CORE");
  await panel.getByRole("button", { name: "Accept pillar" }).click();
  await panel.locator(".thesis-card-top h4", { hasText: "Durable profitability" }).waitFor();
  assert.equal(await panel.locator(".thesis-state", { hasText: "UNPROVEN" }).count(), 1);
  await panel.getByRole("heading", { name: "What's Working" }).waitFor();
  await panel.getByRole("heading", { name: "What Needs Proof" }).waitFor();
  await panel.getByRole("heading", { name: "What Worries Me" }).waitFor();
  assert.equal(await panel.getByLabel("Reason for this revision").count(), 0);
  await panel.locator(".thesis-date-control > summary").click();
  await panel.getByLabel("History as of").fill("2020-01-01");
  await panel.getByText("Current Investment Case · 2020-01-01").waitFor();
  assert.equal(await panel.locator(".thesis-card-top").count(), 0);
  await panel.getByRole("button", { name: "Return to current thesis" }).click();
  await panel.locator(".thesis-card-top h4", { hasText: "Durable profitability" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "mobile overflow");
  await panel.screenshot({ path: "/tmp/almonds-phase81-thesis-mobile.png" });
  assert.deepEqual(errors, []);
  console.log("PASS: simple empty state, optional editing, pillar card, summary sections, historical view, mobile layout");
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

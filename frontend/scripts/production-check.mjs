// Read-only smoke check against the actual local production deployment.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const origin = process.env.ALMONDS_PRODUCTION_URL || "http://127.0.0.1:8000";
const response = await fetch(`${origin}/api/companies`);
assert.ok(response.ok);
const companies = await response.json();
assert.ok(companies.length > 0);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/#financials`, { waitUntil: "networkidle" });
  const companySelect = page.locator("#financials .financial-company select");
  await companySelect.waitFor();
  assert.equal(await companySelect.locator("option").count(), companies.length);
  const panel = page.getByRole("region", { name: "Forecast Accuracy", exact: true });
  await panel.getByRole("heading", { name: "Forecast Accuracy", exact: true }).waitFor();
  const samples = [...new Set([companies[0].symbol, "ESDS", "ETERNAL", "GOLDBEES", companies.at(-1).symbol])]
    .filter((symbol) => companies.some((company) => company.symbol === symbol));
  for (const symbol of samples) {
    if ((await companySelect.inputValue()) !== symbol) {
      const loaded = page.waitForResponse((item) => item.url().includes(`/api/companies/${symbol}/forecasts/accuracy?`));
      await companySelect.selectOption(symbol);
      assert.equal((await loaded).status(), 200);
    }
    await panel.getByRole("heading", { name: "Forecast Accuracy", exact: true }).waitFor();
    await panel.getByText(/^Insufficient data · Minimum \d+ observations/).waitFor();
    assert.equal(await panel.locator("form").count(), 0);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await panel.getByRole("heading", { name: "Forecast Accuracy", exact: true }).isVisible());
  assert.deepEqual(errors, []);
  await mkdir("e2e-artifacts", { recursive: true });
  await page.screenshot({ path: "e2e-artifacts/production-tracking.png", fullPage: true });
  console.log(`Production browser passed: ${companies.length} selectable portfolio entries; ${samples.join(", ")}; desktop/mobile; no browser errors.`);
} finally {
  await browser.close();
}

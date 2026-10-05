// Read-only browser check against the existing local ESDS / ETERNAL corpus.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.goto(process.env.ALMONDS_BASE_URL || "http://127.0.0.1:5173/", {
    waitUntil: "networkidle",
  });
  const memory = page.locator("#memory");
  await memory
    .getByRole("combobox", { name: "Company", exact: true })
    .selectOption("ETERNAL");
  await memory
    .getByRole("button", {
      name: "What explained margin changes?",
      exact: true,
    })
    .click();
  await memory
    .getByRole("button", { name: "Search evidence", exact: true })
    .click();
  await memory.locator(".memory-passage").first().waitFor();
  assert.ok(
    await memory.locator('a[href*="/api/documents/4/file#page="]').count(),
  );
  assert.match(await memory.innerText(), /MANUAL/);
  await mkdir("e2e-artifacts", { recursive: true });
  await memory.screenshot({ path: "e2e-artifacts/phase7-memory-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  await memory.screenshot({ path: "e2e-artifacts/phase7-memory-mobile.png" });
  await memory.getByLabel("Known by date").fill("2020-01-01");
  await memory
    .getByRole("button", { name: "Search evidence", exact: true })
    .click();
  await memory
    .getByText("No matching historical evidence for these filters.", {
      exact: true,
    })
    .waitFor();
  assert.equal(await memory.locator(".memory-passage").count(), 0);
  await memory
    .getByRole("combobox", { name: "Company", exact: true })
    .selectOption("ESDS");
  await memory
    .getByRole("button", { name: "Search evidence", exact: true })
    .click();
  await memory
    .getByText("No matching historical evidence for these filters.", {
      exact: true,
    })
    .waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: retrieved source passages, manual provenance, temporal exclusion, company switching, mobile layout, no page errors",
  );
} finally {
  await browser.close();
}

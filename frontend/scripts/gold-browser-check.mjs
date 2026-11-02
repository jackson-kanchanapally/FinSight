import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const frontend = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const prices = {
  global: {
    usd_per_oz: "4000",
    as_of: "2026-11-02T05:15:00Z",
    source_url: "https://gold-api.com/",
    freshness: "FRESH",
    change_pct: "-0.50",
  },
  india: {
    city: "India",
    inr_22k_per_g: "11200",
    inr_24k_per_g: "12200",
    change_22k_pct: "-0.40",
    change_24k_pct: "-0.40",
    rate_date: "2026-11-01",
    source_url: "https://groww.in/gold-rates",
    freshness: "PREVIOUS_DAY",
    basis: "Before GST and making charges",
  },
  hyderabad: {
    city: "Hyderabad",
    inr_22k_per_g: "11000",
    inr_24k_per_g: "12000",
    change_22k_pct: "-0.50",
    change_24k_pct: "-0.50",
    rate_date: "2026-11-02",
    source_url: "https://groww.in/gold-rates/gold-rate-today-in-hyderabad",
    freshness: "CURRENT",
    basis: "Before GST and making charges",
  },
};
const fixture = {
  api_version: 2,
  prices,
  analysis: {
    bias: "NEGATIVE",
    summary: "An invented demonstration headline reports pressure on gold.",
    what_next:
      "A weaker dollar could offer support; further dollar strength could pressure gold.",
    method: "Dated headline reading with conditional scenarios.",
    local_context: {
      india:
        "India reference context: the rupee and local pricing can change INR rates.",
      hyderabad:
        "Hyderabad reference context: the latest published local rate is shown.",
    },
    comparison_note:
      "The global and retail comparisons cover different periods.",
    drivers: [
      {
        headline_id: 1,
        title: "Demo: Gold falls as the dollar strengthens",
        source: "FXStreet",
        url: "https://www.fxstreet.com/news/demo",
        impact: "NEGATIVE",
        reason:
          "A stronger dollar makes gold more expensive for buyers using other currencies. That can reduce demand.",
        factor: "A stronger US dollar",
        scope: "GLOBAL",
      },
    ],
  },
  headlines: [
    {
      id: 1,
      title: "Demo: Gold falls as the dollar strengthens",
      source: "FXStreet",
      url: "https://www.fxstreet.com/news/demo",
      published_at: "2026-11-02T05:00:00Z",
      impact: "NEGATIVE",
      impact_reason: "The headline reports a decline.",
      important: true,
    },
  ],
  history: [
    { recorded_on: "2026-11-02", fetched_at: "2026-11-02T05:15:00Z", prices },
  ],
  status: {
    enabled: true,
    telegram_connected: true,
    last_refresh_at: "2026-11-02T05:15:00Z",
    last_success_at: "2026-11-02T05:15:00Z",
    errors: [],
    schedule: [
      { name: "morning", time: "10:45" },
      { name: "noon", time: "13:00" },
      { name: "evening", time: "21:00" },
    ],
    next_alert_at: "2026-11-02T07:30:00Z",
    timezone: "Asia/Kolkata",
    poll_minutes: 15,
    move_threshold_pct: 1,
    deliveries: [],
  },
};

let vite, browser;
try {
  vite = await createServer({
    root: frontend,
    server: { host: "127.0.0.1", port: 0 },
  });
  await vite.listen();
  const address = vite.httpServer.address();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let refreshes = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let json = [];
    if (url.pathname === "/api/portfolio")
      json = { holdings: [], realised_pnl: "0" };
    else if (url.pathname === "/api/telegram/status")
      json = { enabled: false, configured: false, counts: {} };
    else if (url.pathname === "/api/gold") json = fixture;
    else if (url.pathname === "/api/gold/refresh") {
      refreshes++;
      if (refreshes === 1) {
        fixture.prices.global.usd_per_oz = "4005";
        json = { status: "OK", queued: 0, errors: [] };
      } else {
        fixture.prices.global.freshness = "STALE";
        fixture.status.errors = [
          { source: "Global gold", reason: "CONNECTION_OR_SCHEMA_ERROR" },
        ];
        json = { status: "PARTIAL", queued: 0, errors: fixture.status.errors };
      }
    }
    await route.fulfill({ json });
  });
  await page.goto(`http://127.0.0.1:${address.port}/#gold`);
  const section = page.locator("#gold");
  await section
    .getByRole("heading", { name: "Gold watch", exact: true })
    .waitFor();
  await section.getByText("$4,000.00", { exact: true }).first().waitFor();
  assert.equal(await page.locator(".breadcrumb strong").innerText(), "Gold");
  assert.equal(await page.locator(".hero").count(), 0);
  assert.equal(await section.locator(".gold-price-card").count(), 3);
  const picker = section.getByRole("group", { name: "Indian rate location" });
  assert.equal(
    await picker
      .getByRole("button", { name: "Hyderabad", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await picker.getByRole("button", { name: "India", exact: true }).click();
  await section.getByText("₹12,200.00", { exact: true }).first().waitFor();
  assert.match(
    await section.locator(".gold-price-grid").innerText(),
    /INDIA REFERENCE/,
  );
  assert.match(
    await section.locator(".gold-price-grid").innerText(),
    /1 Nov 2026/,
  );
  assert.match(
    await section.locator(".gold-local-context").innerText(),
    /India reference context/,
  );
  await page.reload();
  await section.getByText("₹12,200.00", { exact: true }).first().waitFor();
  assert.equal(
    await picker
      .getByRole("button", { name: "India", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.match(
    await section.locator(".gold-schedule").innerText(),
    /10:45 IST/,
  );
  assert.match(
    await section.locator(".gold-schedule").innerText(),
    /13:00 IST/,
  );
  assert.match(
    await section.locator(".gold-schedule").innerText(),
    /21:00 IST/,
  );
  await page.locator('.side-nav a[href="#overview"]').click();
  const compact = page.locator("#gold-overview");
  await compact.getByText("$4,000.00", { exact: true }).waitFor();
  await compact.getByText("₹12,200.00", { exact: true }).waitFor();
  await compact.getByRole("button", { name: "Hyderabad", exact: true }).click();
  await compact.getByRole("link", { name: "Open gold watch" }).click();
  await section.getByText("₹12,000.00", { exact: true }).first().waitFor();
  assert.equal(
    await picker
      .getByRole("button", { name: "Hyderabad", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await section.getByRole("button", { name: "Refresh gold" }).click();
  await section
    .getByRole("status")
    .getByText("Gold prices and news updated.")
    .waitFor();
  assert.equal(refreshes, 1);
  await section.getByText("$4,005.00", { exact: true }).first().waitFor();
  await section.getByRole("button", { name: "Refresh gold" }).click();
  await section
    .getByRole("alert")
    .getByText("Some sources could not update")
    .waitFor();
  assert.equal(
    await section.locator('.gold-price-card[data-stale="true"]').count(),
    1,
  );
  assert.match(await section.innerText(), /Old quote/);
  await section.locator(".gold-history summary").first().click();
  assert.match(
    await section.locator(".gold-history table").innerText(),
    /12,000/,
  );
  await picker.getByRole("button", { name: "India", exact: true }).click();
  assert.match(
    await section.locator(".gold-history table").innerText(),
    /India 22K/i,
  );
  assert.match(
    await section.locator(".gold-history table").innerText(),
    /12,200/,
  );
  const artifact = path.join(frontend, "e2e-artifacts");
  await mkdir(artifact, { recursive: true });
  await section.screenshot({ path: path.join(artifact, "gold-desktop.png") });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
      `Overflow at ${width}px`,
    );
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await section.screenshot({ path: path.join(artifact, "gold-mobile.png") });
  assert.deepEqual(errors, []);
  console.log(
    "Gold India/Hyderabad selection, persistence, independent dates, reasoning, refresh, history and mobile checks passed",
  );
} finally {
  await browser?.close();
  await vite?.close();
}

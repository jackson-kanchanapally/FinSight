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
const common = {
  stage: "UPCOMING",
  sector: "Software",
  opens_on: "2026-11-06",
  closes_on: "2026-11-10",
  lists_on: "2026-11-13",
  price_low: "95",
  price_high: "100",
  issue_cr: "500",
  fresh_cr: "400",
  ofs_cr: "100",
  lot_size: 150,
  gmp_at: "2026-11-02T02:30:00Z",
  min_investment: "15000",
  subscription: null,
  annual_roe: "20",
  fetched_at: "2026-11-02T02:30:00Z",
  business: "Invented demonstration business for browser testing.",
  objects: ["Working capital"],
  source_url: "https://www.investorgain.com/",
  rhp_url: null,
};
const assessment = {
  status: "ATTRACTIVE",
  missing: [],
  reasons: [],
  risks: ["GMP is unofficial"],
  metrics: {
    annual_period: "2026-03-31",
    income_cr: "1000",
    pat_cr: "100",
    pe: "20",
    debt_to_equity: "0.25",
  },
  method: "Rules-based financial screen",
};
const fixture = {
  issues: [
    {
      ...common,
      id: 2,
      name: "Demo Low Premium",
      gmp: "5",
      gmp_pct: "5.00",
      assessment: {
        ...assessment,
        status: "WATCH",
        reasons: ["GMP below 10% screen"],
      },
    },
    {
      ...common,
      id: 3,
      name: "Demo Missing Quote",
      gmp: null,
      gmp_pct: null,
      assessment: {
        ...assessment,
        status: "INSUFFICIENT_DATA",
        missing: ["gmp"],
      },
    },
    {
      ...common,
      id: 1,
      name: "Demo High Premium",
      gmp: "40",
      gmp_pct: "40.00",
      assessment,
    },
  ],
  status: {
    enabled: true,
    telegram_enabled: true,
    telegram_configured: true,
    last_success_at: "2026-11-02T02:30:00Z",
    last_error: null,
    schedule: "Monday 08:00 IST weekly; daily pre-opening review",
    screen: { min_gmp_pct: 10, max_pe: 40, gmp_max_age_hours: 24 },
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
    else if (url.pathname === "/api/ipos") json = fixture;
    else if (url.pathname === "/api/ipos/refresh") {
      refreshes++;
      json = { status: "OK", stored: 3, queued: 0 };
    }
    await route.fulfill({ json });
  });
  await page.goto(`http://127.0.0.1:${address.port}/#ipos`);
  const section = page.locator("#ipos");
  await section
    .getByRole("heading", { name: "Mainboard IPOs", exact: true })
    .waitFor();
  assert.equal(await page.locator(".breadcrumb strong").innerText(), "IPOs");
  assert.equal(await page.locator(".hero").count(), 0);
  assert.deepEqual(await section.locator(".ipo-card h3").allTextContents(), [
    "Demo High Premium",
    "Demo Low Premium",
    "Demo Missing Quote",
  ]);
  assert.equal(
    await section.locator('.ipo-premium[data-premium="missing"]').count(),
    1,
  );
  await page.locator('.side-nav a[href="#overview"]').click();
  await page.getByText("Portfolio snapshot").waitFor();
  assert.equal(await page.locator("#ipos").count(), 0);
  await page.locator('.side-nav a[href="#ipos"]').click();
  await section
    .getByRole("heading", { name: "Mainboard IPOs", exact: true })
    .waitFor();
  await section.getByLabel("Show attractive IPOs only").check();
  assert.equal(await section.locator(".ipo-card").count(), 1);
  await section.locator(".ipo-analysis summary").click();
  assert.match(
    await section.locator(".ipo-analysis").innerText(),
    /Working capital/,
  );
  await section.getByRole("button", { name: "Refresh IPOs" }).click();
  await section
    .getByRole("status")
    .getByText(/Checked 3 mainboard IPOs/)
    .waitFor();
  assert.equal(refreshes, 1);
  await section.getByLabel("Show attractive IPOs only").uncheck();
  await section.scrollIntoViewIfNeeded();
  const artifact = path.join(frontend, "e2e-artifacts");
  await mkdir(artifact, { recursive: true });
  await section.screenshot({ path: path.join(artifact, "ipos-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await section.scrollIntoViewIfNeeded();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  await section.screenshot({ path: path.join(artifact, "ipos-mobile.png") });
  assert.deepEqual(errors, []);
  console.log(
    "IPO desktop/mobile, ranking, filtering, analysis, refresh and overflow checks passed",
  );
} finally {
  await browser?.close();
  await vite?.close();
}

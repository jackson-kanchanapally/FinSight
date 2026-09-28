import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const projectDir = path.resolve(frontendDir, "..");
const backendDir = path.join(projectDir, "backend");
const backendPython =
  process.env.ALMONDS_BACKEND_PYTHON ||
  process.env.FINSIGHT_BACKEND_PYTHON ||
  path.join(backendDir, ".venv", "bin", "python");
const artifactDir = path.join(frontendDir, "e2e-artifacts");

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) =>
    server.once("error", reject).listen(0, "127.0.0.1", resolve),
  );
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

async function waitForApi(port, processRef) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (processRef.exitCode !== null)
      throw new Error(`Test API exited with code ${processRef.exitCode}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for the isolated test API");
}

async function run() {
  const apiPort = await freePort();
  const apiProcess = spawn(
    backendPython,
    [
      "-m",
      "uvicorn",
      "tests.browser_app:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(apiPort),
      "--log-level",
      "warning",
    ],
    {
      cwd: backendDir,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let apiErrors = "";
  apiProcess.stderr.on("data", (chunk) => {
    apiErrors += chunk.toString();
  });
  let vite;
  let browser;

  try {
    await waitForApi(apiPort, apiProcess);
    vite = await createServer({
      configFile: path.join(frontendDir, "vite.config.ts"),
      root: frontendDir,
      server: {
        host: "127.0.0.1",
        port: 0,
        proxy: {
          "/api": `http://127.0.0.1:${apiPort}`,
          "/health": `http://127.0.0.1:${apiPort}`,
        },
      },
    });
    await vite.listen();
    const address = vite.httpServer?.address();
    assert.ok(address && typeof address !== "string");

    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(`http://127.0.0.1:${address.port}/`, {
      waitUntil: "networkidle",
    });
    await page.getByText("Portfolio snapshot").waitFor();
    assert.equal(await page.title(), "almonds — Portfolio overview");
    assert.match(
      await page.locator('.brand[aria-label="almonds overview"]').innerText(),
      /almonds/,
    );
    assert.equal(await page.locator(".brand-mark").evaluate((img) => img.naturalWidth), 64);
    assert.equal(await page.locator('link[rel="icon"]').getAttribute("href"), "/almonds-mark.svg");
    const theme = await page.evaluate(() => ({
      page: getComputedStyle(document.documentElement).backgroundColor,
      panel: getComputedStyle(document.querySelector(".stat-card"))
        .backgroundColor,
    }));
    assert.deepEqual(theme, {
      page: "rgb(8, 8, 8)",
      panel: "rgb(22, 22, 22)",
    });

    await page.getByRole("button", { name: "Add company" }).first().click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel(/Symbol/).fill("esds");
    await dialog.getByLabel(/Company name/).fill("ESDS Software Solution");
    await dialog.getByRole("button", { name: "Add company" }).click();
    await page
      .getByRole("status")
      .getByText(/ESDS added/)
      .waitFor();
    await page.locator('a[href="#valuation"]').click();
    await page.locator('#valuation').waitFor();
    assert.match(await page.locator('#valuation').innerText(), /Valuation/);

    await page
      .getByRole("button", { name: "Record transaction" })
      .first()
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel(/Quantity/).fill("34");
    await dialog.getByLabel(/Price per share/).fill("429");
    await dialog.getByLabel(/Trade date/).fill("2026-09-01");
    await dialog.getByRole("button", { name: "Record buy" }).click();
    await page
      .getByRole("status")
      .getByText(/BUY transaction recorded/)
      .waitFor();

    await page
      .getByRole("button", { name: "Record transaction" })
      .first()
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Sell shares" }).click();
    await dialog.getByLabel(/Quantity/).fill("24");
    await dialog.getByLabel(/Price per share/).fill("1191");
    await dialog.getByLabel(/Trade date/).fill("2026-09-10");
    await dialog.getByRole("button", { name: "Record sell" }).click();
    await page
      .getByRole("status")
      .getByText(/SELL transaction recorded/)
      .waitFor();

    const holding = await page.locator("#holdings tbody tr").innerText();
    for (const expected of [
      "ESDS",
      "10",
      "₹429.00",
      "₹4,290.00",
      "₹18,288.00",
    ]) {
      assert.ok(
        holding.includes(expected),
        `Missing ${expected} in holdings: ${holding}`,
      );
    }
    assert.equal(await page.locator("#activity tbody tr").count(), 2);
    await page.getByRole("button", { name: "SELL", exact: true }).click();
    assert.equal(await page.locator("#activity tbody tr").count(), 1);
    await page.getByRole("button", { name: "All trades" }).click();

    await page
      .getByRole("button", { name: "Record transaction" })
      .first()
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Sell shares" }).click();
    await dialog.getByLabel(/Quantity/).fill("11");
    await dialog.getByLabel(/Price per share/).fill("100");
    await dialog.getByRole("button", { name: "Record sell" }).click();
    await dialog
      .getByRole("alert")
      .getByText(/Only 10 shares/)
      .waitFor();
    await dialog.getByRole("button", { name: "Cancel" }).click();

    await page.getByRole("button", { name: "Check for events" }).click();
    await page.locator(".event-row").first().waitFor();
    assert.equal(await page.locator(".event-row").count(), 4);
    await page
      .locator(".event-row")
      .filter({ hasText: "Unaudited Financial Results" })
      .click();
    await page
      .getByRole("heading", { name: /Unaudited Financial Results/ })
      .waitFor();
    assert.equal(await page.locator(".event-document").count(), 1);
    assert.ok(
      (await page.locator(".event-document").innerText()).includes("2 pages"),
    );
    assert.ok(
      (await page.locator(".fixture-notice").innerText()).includes(
        "not a live company filing",
      ),
    );
    await mkdir(artifactDir, { recursive: true });
    await page.screenshot({
      path: path.join(artifactDir, "event-detail.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "Back to events" }).click();
    await page.getByRole("button", { name: "Check for events" }).click();
    await page.getByRole("status").getByText(/0 new/).waitFor();

    await page.getByRole("button", { name: "Add company" }).first().click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel(/Symbol/).fill("TESTCO");
    await dialog.getByLabel(/Company name/).fill("Test Company");
    await dialog.getByRole("button", { name: "Add company" }).click();
    await page.getByRole("status").getByText(/TESTCO added/).waitFor();
    const companyRows = page.locator("#companies .company-row");
    assert.equal(await companyRows.count(), 2);
    const firstRow = await companyRows.nth(0).boundingBox();
    const secondRow = await companyRows.nth(1).boundingBox();
    assert.ok(firstRow && secondRow && secondRow.y > firstRow.y,
      "Companies should occupy separate full-width rows");

    await page.locator(".toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: path.join(artifactDir, "desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(
      () =>
        getComputedStyle(document.querySelector(".side-nav a")).fontSize ===
        "0px",
    );
    const mobile = await page.evaluate(() => ({
      viewport: innerWidth,
      pageWidth: document.documentElement.scrollWidth,
      navWidth: document.querySelector(".side-nav").getBoundingClientRect()
        .width,
      navScrollWidth: document.querySelector(".side-nav").scrollWidth,
    }));
    assert.ok(
      mobile.pageWidth <= mobile.viewport,
      `Page overflows on mobile: ${JSON.stringify(mobile)}`,
    );
    assert.ok(
      mobile.navScrollWidth <= mobile.navWidth,
      `Navigation clips on mobile: ${JSON.stringify(mobile)}`,
    );
    assert.equal(await companyRows.count(), 2);
    await page.screenshot({
      path: path.join(artifactDir, "mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(pageErrors, []);
    console.log(
      "Browser check passed: ESDS accounting, event ingestion and details, deduplication, oversell validation, dark theme, mobile layout, and no page errors.",
    );
    console.log(
      `Screenshots: ${artifactDir}/desktop.png and ${artifactDir}/mobile.png`,
    );
  } catch (error) {
    if (apiErrors) console.error(apiErrors);
    throw error;
  } finally {
    await browser?.close();
    await vite?.close();
    apiProcess.kill("SIGTERM");
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

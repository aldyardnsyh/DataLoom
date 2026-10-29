/**
 * Demo regression suite.
 *
 * Run through the global app runner so the server is started, health-checked,
 * and killed automatically:
 *
 *   python C:\Users\aldya\.agents\skills\browser-qa-py\qa_app.py \
 *     --app-cmd "npm run start -- -p 3210" \
 *     --workdir apps/web \
 *     --url "http://127.0.0.1:3210/?mode=demo" \
 *     --cmd "node --test e2e/demo.e2e.mjs"
 *
 * The suite uses the Playwright library directly, so it needs no extra
 * @playwright/test dependency and no webServer block that could start a dev
 * server by accident. Screenshots go to the OS temp directory, not the repo.
 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { after, before, beforeEach, test } from "node:test";

import { chromium } from "playwright";

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:3210";
const ARTIFACTS = process.env.E2E_ARTIFACTS ?? path.join(os.tmpdir(), "dataloom-e2e");
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

let browser;
let page;
let consoleErrors = [];

async function goto(pathname) {
  await page.goto(`${BASE_URL}${pathname}`, { waitUntil: "domcontentloaded" });
  await page.locator("main, h1").first().waitFor({ state: "visible" });
  // Demo queries resolve in ~60ms and the demo banner is set in an effect.
  // Wait for that paint so assertions do not race the first hydration pass.
  await page.waitForTimeout(250);
}

async function shot(name) {
  const file = path.join(ARTIFACTS, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function assertNoOverflow() {
  const ok = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  );
  assert.equal(ok, true, "page must not overflow horizontally");
}

function demoLabelCount(text) {
  return (text.match(/Mode demo/g) ?? []).length;
}

before(async () => {
  await mkdir(ARTIFACTS, { recursive: true });
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: DESKTOP });
  page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
});

beforeEach(() => {
  consoleErrors = [];
});

after(async () => {
  await browser?.close();
});

test("landing explains the quality gate without overflow", async () => {
  await goto("/?mode=demo");
  assert.match(await page.locator("h1").innerText(), /Membersihkan data/i);
  assert.ok((await page.getByRole("button", { name: /Coba demo tanpa akun/i }).count()) >= 1);
  await assertNoOverflow();
  await shot("e2e-01-landing");
  assert.deepEqual(consoleErrors, []);
});

test("shell shows one demo label and collapses to a 64px rail", async () => {
  await goto("/dashboard?mode=demo");
  await page.getByText("Mode demo", { exact: true }).first().waitFor({ state: "visible" });
  assert.equal(demoLabelCount(await page.locator("body").innerText()), 1);

  await page.getByRole("button", { name: "Ciutkan menu navigasi" }).click();
  await page.waitForTimeout(450);
  const width = await page
    .locator('[data-sidebar="sidebar"]')
    .first()
    .evaluate((element) => Math.round(element.getBoundingClientRect().width));
  assert.equal(width, 64);
  await shot("e2e-02-sidebar-rail");

  await page.getByRole("button", { name: "Perluas menu navigasi" }).click();
  await page.waitForTimeout(350);
  assert.deepEqual(consoleErrors, []);
});

test("quarantine tab exposes rejected rows, run ledger, and gate", async () => {
  await goto("/data?mode=demo");
  await page
    .getByText("quarantine_orders", { exact: true })
    .first()
    .waitFor({ state: "visible", timeout: 15_000 });
  await page.getByText("quarantine_orders", { exact: true }).first().click();
  await page.getByText("Run yang menolak baris").waitFor({ state: "visible" });

  const body = await page.locator("body").innerText();
  assert.match(body, /Karantina & Rekonsiliasi/);
  assert.match(body, /Rasio karantina run/);
  assert.match(body, /Gerbang promosi/);
  assert.match(body, /_reason_code|not_null|unique/);
  await assertNoOverflow();
  await shot("e2e-03-quarantine");
  assert.deepEqual(consoleErrors, []);
});

test("running a pipeline produces a completed timeline and gate", async () => {
  await goto("/pipelines/pl-1?mode=demo");
  const runButton = page.getByRole("button", { name: "Jalankan", exact: true });
  await runButton.waitFor({ state: "visible", timeout: 15_000 });
  await page.waitForFunction(
    () => {
      const buttons = [...document.querySelectorAll("button")];
      const target = buttons.find((button) => button.textContent?.trim() === "Jalankan");
      return Boolean(target && !target.disabled);
    },
    { timeout: 15_000 },
  );
  await runButton.click();
  await page.waitForURL(/\/runs\/run-/, { timeout: 10_000 });
  await page.locator("main").waitFor({ state: "visible" });
  await page.getByText("Daftar periksa gerbang").waitFor({ state: "visible", timeout: 15_000 });
  const body = await page.locator("body").innerText();
  assert.match(body, /Berhasil|Lulus/);
  assert.match(body, /Daftar periksa gerbang/);
  await shot("e2e-05-run-completed");
  assert.deepEqual(consoleErrors, []);
});

test("data contract prints policy, gate, and row rules", async () => {
  await goto("/data-contract?mode=demo");
  const body = await page.locator("body").innerText();
  assert.match(body, /Kebijakan proyek/);
  assert.match(body, /schema_valid/);
  assert.match(body, /Aturan validasi baris/);
  assert.match(body, /quarantine_ratio/);
  await assertNoOverflow();
  await shot("e2e-04-contract");
  assert.deepEqual(consoleErrors, []);
});

test("mobile demo has no overflow and opens the sidebar sheet", async () => {
  const context = await browser.newContext({ viewport: MOBILE });
  const mobile = await context.newPage();
  const errors = [];
  mobile.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  mobile.on("pageerror", (error) => errors.push(error.message));

  await mobile.goto(`${BASE_URL}/dashboard?mode=demo`, { waitUntil: "domcontentloaded" });
  await mobile.locator("h1").waitFor({ state: "visible" });
  assert.equal(
    await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    true,
  );

  await mobile.getByRole("button", { name: "Buka menu navigasi" }).click();
  await mobile.getByRole("dialog").waitFor({ state: "visible" });
  await mobile.screenshot({
    path: path.join(ARTIFACTS, "e2e-05-mobile-sheet.png"),
    fullPage: true,
  });
  await mobile.keyboard.press("Escape");
  await mobile.close();
  await context.close();

  assert.deepEqual(errors, []);
});
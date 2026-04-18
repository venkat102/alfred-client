import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for Alfred chat UI smoke tests.
 *
 * All test-env settings are driven by env vars so the same config runs
 * against a local dev bench, a CI ephemeral bench, or a staging site
 * without code changes. Defaults assume a local `bench start` on port
 * 8000 with site dev.alfred.
 *
 * Required env:
 *   ALFRED_BASE_URL   default http://localhost:8000
 *   ALFRED_USER       default Administrator
 *   ALFRED_PASSWORD   default admin
 *
 * Optional env:
 *   ALFRED_HEADLESS   default true (set "false" to watch the browser)
 *   ALFRED_TIMEOUT_MS default 60000 per test (Alfred runs are slow-ish)
 */
export default defineConfig({
	testDir: "./tests",
	timeout: Number(process.env.ALFRED_TIMEOUT_MS || 60_000),
	expect: { timeout: 10_000 },
	fullyParallel: false, // bench site is stateful; serial tests are safer
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	workers: 1,
	reporter: [["list"], ["html", { open: "never" }]],
	use: {
		baseURL: process.env.ALFRED_BASE_URL || "http://localhost:8000",
		headless: process.env.ALFRED_HEADLESS !== "false",
		screenshot: "only-on-failure",
		trace: "retain-on-failure",
		video: "retain-on-failure",
	},
	projects: [
		{
			name: "chromium",
			use: { ...devices["Desktop Chrome"] },
		},
	],
});

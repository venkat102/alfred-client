import { expect, test } from "@playwright/test";
import {
	login,
	openAlfredChat,
	selectMode,
	sendPrompt,
	startNewConversation,
	waitForAgentReply,
} from "./fixtures";

/**
 * Dev-mode end-to-end: send a build prompt, watch the crew run, the
 * preview panel opens with a changeset, click Approve, see the deploy
 * progress, confirm the success banner.
 *
 * This test REQUIRES:
 *   - Ollama reachable at the configured FALLBACK_LLM_BASE_URL
 *   - The processing app running
 *   - The Frappe site writable (no Pending changesets on dev.alfred)
 *
 * Slow: expect 2-10 minutes end-to-end depending on the model. Gate
 * under ALFRED_RUN_SLOW_TESTS so a default `npm test` skips it.
 */
test.skip(
	!process.env.ALFRED_RUN_SLOW_TESTS,
	"Slow LLM + deploy test. Set ALFRED_RUN_SLOW_TESTS=1 to run.",
);

test("dev prompt -> preview -> approve -> deploy success", async ({ page }) => {
	await login(page);
	await openAlfredChat(page);
	await startNewConversation(page);
	await selectMode(page, "dev");

	// A deliberately small build so the test doesn't take forever. The
	// target doctype (ToDo) exists on every Frappe install.
	await sendPrompt(
		page,
		"Add a priority custom field to ToDo with options Low, Medium, High",
	);
	await waitForAgentReply(page, 600_000);

	// Preview panel should open once the changeset is ready. The panel
	// container class is stable (structural); the approve button uses
	// a data-testid because the button class has churned twice.
	const preview = page.locator(".alfred-preview-panel");
	await expect(preview).toBeVisible({ timeout: 600_000 });

	// The preview shows at least one changeset item.
	await expect(preview.locator(".alfred-changeset-item").first()).toBeVisible();

	// Click Approve and wait for the deploy-success banner.
	await page.getByTestId("alfred-preview-approve").click();
	await expect(
		page.locator(".alfred-deploy-success, .alfred-deploy-complete"),
	).toBeVisible({ timeout: 120_000 });
});

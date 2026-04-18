import { expect, test } from "@playwright/test";
import {
	login,
	openAlfredChat,
	selectMode,
	startNewConversation,
} from "./fixtures";

/**
 * Mode switcher persists its selection to the backend via
 * set_conversation_mode, and the choice survives a page reload.
 *
 * This test lives here instead of in a pure backend test because the
 * switcher's UI state is where regressions show up - backend tests
 * already cover the API endpoint itself.
 */
test("mode switcher persists across page reload", async ({ page }) => {
	await login(page);
	await openAlfredChat(page);
	await startNewConversation(page);

	await selectMode(page, "Plan");
	// Reload; the persisted conversation mode should rehydrate.
	await page.reload();
	await expect(page.locator(".alfred-conversation-list")).toBeVisible();
	await expect(
		page.locator(".alfred-mode-switcher .is-active:has-text('Plan')"),
	).toBeVisible();
});

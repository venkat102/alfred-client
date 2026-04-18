import { expect, Page } from "@playwright/test";

/**
 * Shared helpers used by every spec. Keep login, chat open, and
 * prompt send in one place so UI churn only breaks one file.
 *
 * All selectors target the Vue component classes declared in
 * alfred_client/public/js/alfred_chat/*.vue. When the components grow
 * `data-testid` attributes (recommended), this file should migrate to
 * those instead - classes are brittle to CSS refactors.
 */

const USER = process.env.ALFRED_USER || "Administrator";
const PASSWORD = process.env.ALFRED_PASSWORD || "admin";

/** Log into Frappe via /login, then land on /app. */
export async function login(page: Page): Promise<void> {
	await page.goto("/login");
	// Frappe's login form uses #login_email + #login_password.
	await page.locator("#login_email").fill(USER);
	await page.locator("#login_password").fill(PASSWORD);
	await page.locator(".btn-login").click();
	await page.waitForURL(/\/app/);
}

/** Open the Alfred chat page and wait for the conversation list to render. */
export async function openAlfredChat(page: Page): Promise<void> {
	await page.goto("/app/alfred");
	// AlfredChatApp mounts under a div Frappe gives us; wait for the
	// conversation list to render as a readiness signal.
	await expect(page.locator(".alfred-conversation-list")).toBeVisible({
		timeout: 15_000,
	});
}

/**
 * Click the "New Conversation" button and wait for the empty chat view.
 * Returns once the input textarea is interactive.
 */
export async function startNewConversation(page: Page): Promise<void> {
	await page.locator(".alfred-new-conversation-btn").click();
	await expect(page.locator(".alfred-chat-input")).toBeVisible();
}

/**
 * Type a prompt and hit Send. Does not wait for a reply - use
 * waitForAgentReply if you need that.
 */
export async function sendPrompt(page: Page, text: string): Promise<void> {
	const input = page.locator(".alfred-chat-input textarea").first();
	await input.fill(text);
	await page.locator(".alfred-chat-send-btn").click();
	// The user bubble should appear immediately (optimistic render).
	await expect(
		page.locator(".alfred-message.alfred-msg-user").last(),
	).toContainText(text);
}

/**
 * Wait until the agent produces at least one non-typing message after
 * the user's last prompt. Timeouts are long because crews can take
 * several minutes on slow LLMs.
 */
export async function waitForAgentReply(page: Page, timeoutMs = 120_000): Promise<void> {
	await expect(
		page.locator(".alfred-message.alfred-msg-agent").last(),
	).toBeVisible({ timeout: timeoutMs });
}

/**
 * Pick a mode from the ModeSwitcher ("Auto" | "Dev" | "Plan" | "Insights").
 * The switcher persists the choice to the backend via set_conversation_mode.
 */
export async function selectMode(
	page: Page,
	mode: "Auto" | "Dev" | "Plan" | "Insights",
): Promise<void> {
	await page.locator(".alfred-mode-switcher").getByRole("button", { name: mode }).click();
	// The chosen button gets an `is-active` class.
	await expect(
		page.locator(`.alfred-mode-switcher .is-active:has-text("${mode}")`),
	).toBeVisible();
}

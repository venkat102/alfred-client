import { expect, test } from "@playwright/test";
import {
	login,
	openAlfredChat,
	sendPrompt,
	startNewConversation,
	waitForAgentReply,
} from "./fixtures";

/**
 * Golden path smoke: open chat, send a simple greeting, get a reply.
 *
 * The greeting is deliberately the kind of prompt that the fast-path
 * short-circuits to chat mode, so this test doesn't require Ollama to
 * be hot or the LLM classifier to run. If this starts failing, the
 * regression is in the WebSocket bridge or the Frappe-side plumbing,
 * not the agent crew.
 */
test("golden path - greeting gets a chat reply without a crew run", async ({ page }) => {
	await login(page);
	await openAlfredChat(page);
	await startNewConversation(page);
	await sendPrompt(page, "hi Alfred");
	await waitForAgentReply(page, 30_000);

	// Chat-mode replies are plain text, no preview panel should open.
	await expect(page.locator(".alfred-preview-panel")).toHaveCount(0);
});

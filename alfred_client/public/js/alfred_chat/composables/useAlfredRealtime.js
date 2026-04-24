/**
 * The 15 frappe.realtime handlers that drive every server -> UI update.
 *
 * This is the biggest single extraction in the F1 refactor: ~400 lines
 * of bridge logic between the processing app's WebSocket events and
 * the chat's reactive state. Moving it out of AlfredChatApp.vue makes
 * it possible to review, add, or change a handler without scrolling
 * past 1500 lines of unrelated code.
 *
 * Events handled (in order):
 *   1.  alfred_connection_status  - WS lifecycle (connected / reconnecting
 *                                   / failed / stopped)
 *   2.  alfred_agent_status       - crew + phase + agent status; the
 *                                   biggest branching handler
 *   3.  alfred_activity           - live tool-call ticker
 *   4.  alfred_question           - clarifier prompt
 *   5.  alfred_preview            - changeset landed; fetch + display
 *   6.  alfred_error              - PIPELINE_BUSY / RATE_LIMIT /
 *                                   OLLAMA_UNHEALTHY / EMPTY_CHANGESET /
 *                                   generic
 *   7.  alfred_info               - CLARIFIER_LATE_RESPONSE /
 *                                   MEMORY_SAVE_FAILED (soft toasts)
 *   8.  alfred_run_cancelled      - graceful user-initiated cancel
 *   9.  alfred_deploy_progress    - per-step deploy status
 *   10. alfred_deploy_complete
 *   11. alfred_deploy_failed
 *   12. alfred_chat_reply         - chat-mode fast reply (no crew)
 *   13. alfred_insights_reply     - insights-mode markdown reply
 *   14. alfred_mode_switch        - orchestrator's mode decision notice
 *   15. alfred_plan_doc           - plan-mode structured output
 *
 * Bind-once: calling setupAlfredRealtime() more than once is a no-op;
 * the internal realtimeBound flag guards against duplicate subscriptions
 * that would otherwise double-render every event.
 *
 * Required refs (mutable):
 *   currentConversation, connectionState, currentActivity, saturationReason,
 *   pipelineMode, pipelineModeSource, isProcessing, statusText, statusState,
 *   inputDisabled, inputPlaceholder, messages, changeset, cancelInFlight,
 *   conversationStatus, deploySteps, isDeployed
 *
 * Required functions:
 *   addActivity(text, level?), updateAgentStatus(data), pushAgentStep(label),
 *   markLastStepDone(), agentStepLabel(phaseOrAgent),
 *   armDisconnectWatchdog(), clearDisconnectWatchdog(),
 *   clearSaturationWatchdog(), stopTimer(), stopPolling()
 *
 * Required accessors:
 *   getCurrentPromptSentAt() -> Date|null
 *     Read-only access to the module-level currentPromptSentAt variable
 *     so alfred_preview can reject stale changesets. Passed as a getter
 *     because the caller mutates it with a plain `let`, not a ref.
 */

// The deps object this composable requires. Used at construction time
// to fail fast (and with a useful name) when a caller forgets one key,
// instead of letting the TypeError("Cannot read properties of undefined")
// surface later inside a realtime handler where it's opaque. The TDZ
// fix in commit 3116da8 was exactly this class of bug - a missed ref.
const _REQUIRED_DEPS = Object.freeze([
	// refs
	"currentConversation", "connectionState", "currentActivity",
	"saturationReason", "pipelineMode", "pipelineModeSource",
	"isProcessing", "statusText", "statusState",
	"inputDisabled", "inputPlaceholder", "messages", "changeset",
	"cancelInFlight", "conversationStatus", "deploySteps", "isDeployed",
	// fns
	"addActivity", "updateAgentStatus", "pushAgentStep",
	"markLastStepDone", "agentStepLabel",
	"armDisconnectWatchdog", "clearDisconnectWatchdog",
	"clearSaturationWatchdog", "stopTimer", "stopPolling",
	// accessors
	"getCurrentPromptSentAt",
]);

export function useAlfredRealtime(deps) {
	if (!deps || typeof deps !== "object") {
		throw new Error(
			"useAlfredRealtime: deps must be an object with the required "
			+ "refs + functions + accessor. See the doc comment at the top "
			+ "of this file for the full list."
		);
	}
	const missing = _REQUIRED_DEPS.filter((k) => deps[k] === undefined);
	if (missing.length > 0) {
		throw new Error(
			`useAlfredRealtime: missing required deps: ${missing.join(", ")}. `
			+ "Declare each as a ref/function in AlfredChatApp.vue's <script setup> "
			+ "before calling useAlfredRealtime. If a TDZ error surfaces (a ref is "
			+ "declared later in the file), hoist it to the top-of-file state block."
		);
	}

	const {
		// refs
		currentConversation,
		connectionState,
		currentActivity,
		saturationReason,
		pipelineMode,
		pipelineModeSource,
		isProcessing,
		statusText,
		statusState,
		inputDisabled,
		inputPlaceholder,
		messages,
		changeset,
		cancelInFlight,
		conversationStatus,
		deploySteps,
		isDeployed,
		// fns
		addActivity,
		updateAgentStatus,
		pushAgentStep,
		markLastStepDone,
		agentStepLabel,
		armDisconnectWatchdog,
		clearDisconnectWatchdog,
		clearSaturationWatchdog,
		stopTimer,
		stopPolling,
		// accessors
		getCurrentPromptSentAt,
	} = deps;

	let realtimeBound = false;

	function setupAlfredRealtime() {
		if (realtimeBound) return;
		realtimeBound = true;

		frappe.realtime.on("alfred_connection_status", (data) => {
			if (!currentConversation.value) return;
			connectionState.value = data.state;
			const level = (data.state === "failed" || data.state === "reconnecting") ? "error" : "info";
			addActivity(data.message || data.state, level);
			if (data.detail) addActivity(data.detail, "error");
			// Clear the live activity ticker when the connection itself dies - otherwise
			// the user sees "Reading X..." frozen in place while the processing app is gone.
			if (data.state === "failed" || data.state === "disconnected" || data.state === "stopped") {
				currentActivity.value = null;
				armDisconnectWatchdog();
			} else {
				clearDisconnectWatchdog();
			}
			// A "connected" event means the manager started and reached the
			// processing app, so any saturation banner we were showing is now
			// stale. Clear it.
			if (data.state === "connected" || data.state === "starting") {
				clearSaturationWatchdog();
				saturationReason.value = null;
			}
		});

		frappe.realtime.on("alfred_agent_status", (data) => {
			if (!currentConversation.value) return;
			// Capture pipeline mode on first event per run so the UI hides the
			// 6-phase pipeline in lite mode and shows the "Basic" badge.
			if (data.pipeline_mode === "full" || data.pipeline_mode === "lite") {
				pipelineMode.value = data.pipeline_mode;
			}
			if (data.pipeline_mode_source === "plan" || data.pipeline_mode_source === "site_config") {
				pipelineModeSource.value = data.pipeline_mode_source;
			}
			updateAgentStatus(data);

			const agent = data.agent || "Agent";

			if (data.status === "enhancing") {
				isProcessing.value = true;
				addActivity(data.message || "Analyzing request...");
				pushAgentStep("Analyzing your request...");
			} else if (data.status === "started" && data.phase) {
				isProcessing.value = true;
				addActivity(`${agent}: started`);
				// Mark previous in-progress step as done
				markLastStepDone();
				pushAgentStep(`${agentStepLabel(data.phase || agent)}...`);
			} else if (data.status === "completed" && agent) {
				isProcessing.value = false;
				stopTimer();
				inputDisabled.value = false;
				inputPlaceholder.value = __("Ask a follow-up or start a new request...");
				statusText.value = __("Completed");
				statusState.value = "success";
				currentActivity.value = null;
				addActivity(`${agent} completed`);
				markLastStepDone();
				// Show the result text in chat
				let content = data.result || `${agent} completed`;
				messages.value.push({
					_id: Date.now(), role: "assistant", message_type: "text",
					content: content,
				});
			} else if (data.event === "crew_started") {
				addActivity("Pipeline started");
				isProcessing.value = true;
			} else if (data.event === "crew_completed") {
				addActivity("Pipeline completed");
				markLastStepDone();
			} else if (data.event) {
				// Other crew lifecycle events - show as agent step in chat
				let label = data.agent ? `${data.agent}: ${data.event}` : data.event;
				addActivity(label);
				if (data.agent && data.event !== "crew_started" && data.event !== "crew_completed") {
					markLastStepDone();
					pushAgentStep(`${data.agent} is working...`);
				}
			} else {
				addActivity(`${agent}: ${data.status}${data.message ? " - " + data.message : ""}`);
			}
		});

		frappe.realtime.on("alfred_activity", (data) => {
			if (!currentConversation.value) return;
			const text = data.description || data.tool || "";
			if (!text) return;
			currentActivity.value = text;
			addActivity(text, "info");
		});

		frappe.realtime.on("alfred_question", (data) => {
			if (!currentConversation.value) return;
			isProcessing.value = false;
			stopTimer();
			inputDisabled.value = false;
			inputPlaceholder.value = __("Type your answer...");
			statusText.value = __("Waiting for your response");
			statusState.value = "waiting";
			messages.value.push({
				_id: Date.now(), role: "agent", message_type: "question",
				content: data.text || data.question || "",
				agent_name: data.agent,
				metadata: JSON.stringify(data),
			});
		});

		frappe.realtime.on("alfred_preview", (data) => {
			if (!currentConversation.value || !data.changeset_name) return;
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_changeset",
				args: { changeset_name: data.changeset_name },
				callback: (r) => {
					if (!r.message) return;
					const cs = r.message;
					// Reject if the changeset was created before the current prompt
					// (could happen if a stale event arrives after a new prompt was sent).
					const promptSentAt = getCurrentPromptSentAt();
					if (promptSentAt && cs.creation && cs.creation < promptSentAt) {
						return;
					}
					changeset.value = cs;
				},
			});
		});

		frappe.realtime.on("alfred_error", (data) => {
			if (!currentConversation.value) return;
			// PIPELINE_BUSY is a soft reject: a previous pipeline is still running
			// on the same conversation. Don't tear down UI state - just show a toast.
			if (data.code === "PIPELINE_BUSY") {
				frappe.show_alert({
					message: data.error || __("A pipeline is already running for this conversation."),
					indicator: "orange",
				});
				return;
			}
			// RATE_LIMIT is a soft reject with useful recovery info in the
			// payload (retry_after seconds, per-hour limit). Show a toast
			// with the human-readable countdown instead of a red error
			// bubble - the conversation itself is fine, just throttled.
			if (data.code === "RATE_LIMIT") {
				const retryAfter = data.retry_after || 60;
				const limit = data.limit;
				const msg = data.error || (
					limit
						? __("Rate limit hit ({0}/hour). Try again in {1}s.", [limit, retryAfter])
						: __("Rate limit hit. Try again in {0}s.", [retryAfter])
				);
				frappe.show_alert({ message: msg, indicator: "orange" }, 8);
				return;
			}
			// OLLAMA_UNHEALTHY is raised by the strict warmup gate when one or more
			// tier models fail a 1-token probe before the crew starts. This is an
			// ops failure, not a user-content failure - keep the conversation open
			// so the user can retry after the admin restarts Ollama, and surface
			// an admin-flagged toast instead of a red error bubble in the chat.
			if (data.code === "OLLAMA_UNHEALTHY") {
				isProcessing.value = false;
				inputDisabled.value = false;
				stopTimer();
				stopPolling();
				statusText.value = __("Processing service unavailable");
				statusState.value = "error";
				currentActivity.value = null;
				frappe.show_alert({
					message: __("Processing service is unavailable - contact your admin."),
					indicator: "red",
				});
				addActivity(__("Ollama health check failed - contact your admin"), "error");
				return;
			}
			// EMPTY_CHANGESET is the "crew finished but produced no deployable
			// change" terminal state. The pipeline attaches a reason slug and
			// an agent_output_preview so the user can see what the agent tried
			// to do. Surface a persistent red toast so the failure is loud,
			// and pack the structured details into the error bubble so the
			// user can inspect the agent's output in place.
			if (data.code === "EMPTY_CHANGESET") {
				isProcessing.value = false;
				inputDisabled.value = false;
				stopTimer();
				stopPolling();
				statusText.value = __("No deployable change");
				statusState.value = "error";
				currentActivity.value = null;
				const userMsg = data.error || data.message || __("Alfred couldn't produce a deployable change.");
				frappe.show_alert(
					{ message: userMsg, indicator: "red" },
					10,
				);
				addActivity(__("Pipeline stopped: no deployable change produced"), "error");
				const detailsParts = [];
				if (data.reason) detailsParts.push(`Reason: ${data.reason}`);
				if (data.drift_reason) detailsParts.push(`Drift: ${data.drift_reason}`);
				if (data.agent_output_preview) {
					detailsParts.push("");
					detailsParts.push("Agent output preview:");
					detailsParts.push(data.agent_output_preview);
				}
				messages.value.push({
					_id: Date.now(), role: "system", message_type: "error",
					content: userMsg,
					details: detailsParts.length ? detailsParts.join("\n") : "",
				});
				return;
			}
			isProcessing.value = false;
			inputDisabled.value = false;
			stopTimer();
			stopPolling();
			statusText.value = __("Error");
			statusState.value = "error";
			currentActivity.value = null;
			addActivity(data.error || data.message || "Error occurred", "error");
			messages.value.push({
				_id: Date.now(), role: "system", message_type: "error",
				content: data.error || data.message || "An error occurred",
			});
		});

		// Non-blocking info notices from the processing app. These are soft
		// signals the pipeline proceeded without - the primary output
		// (changeset / reply) still reached the user, but something
		// adjacent went sideways and they should know. Render as a subtle
		// toast, not an error banner. Codes shipped today:
		//   CLARIFIER_LATE_RESPONSE - user answered after the clarifier
		//     timed out (blue indicator, neutral "heads up")
		//   MEMORY_SAVE_FAILED - conversation memory couldn't persist to
		//     Redis; follow-up turns may be missing context (orange
		//     indicator, warning-level)
		// Unknown codes default to blue - forward-compatible with new codes
		// the server may emit before this client is updated.
		frappe.realtime.on("alfred_info", (data) => {
			if (!currentConversation.value) return;
			if (!data || !data.code) return;  // malformed payload; silently ignore
			const message = data.message || __("Info: {0}", [data.code]);
			const indicator = data.code === "MEMORY_SAVE_FAILED" ? "orange" : "blue";
			frappe.show_alert({ message, indicator }, 6);
		});

		// Graceful user-initiated cancel: the processing app emitted run_cancelled
		// via _send_error because ctx.stop(code="user_cancel") fired. Treat as a
		// neutral outcome, not an error.
		frappe.realtime.on("alfred_run_cancelled", (data) => {
			if (!currentConversation.value) return;
			isProcessing.value = false;
			inputDisabled.value = false;
			cancelInFlight.value = false;
			stopTimer();
			stopPolling();
			statusText.value = __("Cancelled");
			statusState.value = "success";
			currentActivity.value = null;
			conversationStatus.value = "Cancelled";
			addActivity(data?.reason || __("Run cancelled"));
			messages.value.push({
				_id: Date.now(),
				role: "system",
				message_type: "status",
				content: data?.reason || __("Run cancelled."),
			});
		});

		frappe.realtime.on("alfred_deploy_progress", (data) => {
			if (!currentConversation.value) return;
			deploySteps.value = [...deploySteps.value.filter((s) => s.step !== data.step), data];
			addActivity(`Deploy step ${data.step}: ${data.status || "in progress"}`);
		});

		frappe.realtime.on("alfred_deploy_complete", (data) => {
			if (!currentConversation.value) return;
			stopTimer();
			isDeployed.value = true;
			inputDisabled.value = false;
			inputPlaceholder.value = __("Ask a follow-up or start a new request...");
			statusText.value = __("Deployment complete");
			statusState.value = "success";
			addActivity(`Deployment complete - ${data.steps} steps executed`);
			messages.value.push({
				_id: Date.now(), role: "system", message_type: "status",
				content: `Deployment complete! ${data.steps} steps executed successfully.`,
			});
		});

		frappe.realtime.on("alfred_deploy_failed", (data) => {
			if (!currentConversation.value) return;
			stopTimer();
			inputDisabled.value = false;
			statusText.value = __("Deployment failed - rolled back");
			statusState.value = "error";
			addActivity(`Deployment failed at step ${data.step}: ${data.error}`, "error");
			messages.value.push({
				_id: Date.now(), role: "system", message_type: "error",
				content: `Deployment failed at step ${data.step}: ${data.error}. All changes rolled back.`,
			});
		});

		// ── Three-mode chat (Phase A/B) realtime events ───────────────
		// chat_reply: conversational short-circuit from the orchestrator
		// (no crew, no changeset, fast reply).
		frappe.realtime.on("alfred_chat_reply", (data) => {
			if (!currentConversation.value || data.conversation !== currentConversation.value) return;
			isProcessing.value = false;
			inputDisabled.value = false;
			stopTimer();
			stopPolling();
			statusText.value = __("Ready");
			statusState.value = "success";
			currentActivity.value = null;
			messages.value.push({
				_id: Date.now(),
				role: "agent",
				agent_name: "Alfred",
				message_type: "chat_reply",
				content: data.reply || "",
				mode: "chat",
				creation: new Date().toISOString(),
			});
		});

		// insights_reply: read-only Q&A short-circuit (single-agent crew,
		// markdown output, no changeset).
		frappe.realtime.on("alfred_insights_reply", (data) => {
			if (!currentConversation.value || data.conversation !== currentConversation.value) return;
			isProcessing.value = false;
			inputDisabled.value = false;
			stopTimer();
			stopPolling();
			statusText.value = __("Ready");
			statusState.value = "success";
			currentActivity.value = null;
			messages.value.push({
				_id: Date.now(),
				role: "agent",
				agent_name: "Insights",
				message_type: "insights_reply",
				content: data.reply || "",
				mode: "insights",
				// V4: when Alfred detected a report-shaped query, the server
				// attaches a candidate the UI can turn into a one-click Dev
				// handoff via the "Save as Report" button in MessageBubble.
				report_candidate: data.report_candidate || null,
				creation: new Date().toISOString(),
			});
		});

		// mode_switch: orchestrator decision notice. Rendered as a small
		// inline status line so the user can see what mode Alfred picked.
		frappe.realtime.on("alfred_mode_switch", (data) => {
			if (!currentConversation.value || data.conversation !== currentConversation.value) return;
			messages.value.push({
				_id: Date.now(),
				role: "system",
				message_type: "mode_switch",
				content: "",
				mode: data.mode,
				metadata: JSON.stringify({
					mode: data.mode,
					reason: data.reason,
					source: data.source,
					confidence: data.confidence,
				}),
				creation: new Date().toISOString(),
			});
		});

		// plan_doc: Phase C plan mode output. Rendered as a structured panel
		// via MessageBubble -> PlanDocPanel. The user can then click Refine
		// or Approve & Build.
		frappe.realtime.on("alfred_plan_doc", (data) => {
			if (!currentConversation.value || data.conversation !== currentConversation.value) return;
			isProcessing.value = false;
			inputDisabled.value = false;
			stopTimer();
			stopPolling();
			statusText.value = __("Plan ready for review");
			statusState.value = "success";
			currentActivity.value = null;
			messages.value.push({
				_id: Date.now(),
				role: "agent",
				agent_name: "Planner",
				message_type: "plan_doc",
				content: data.plan?.title || __("Plan"),
				plan: data.plan,
				mode: "plan",
				metadata: JSON.stringify({ mode: "plan", plan: data.plan }),
				creation: new Date().toISOString(),
			});
		});
	}

	return { setupAlfredRealtime };
}

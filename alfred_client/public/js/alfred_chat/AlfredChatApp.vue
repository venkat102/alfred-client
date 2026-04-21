<template>
	<div class="alfred-app">
		<!-- Status Bar -->
		<div class="alfred-status-bar">
			<div class="alfred-agent-status">
				<span :class="['alfred-status-dot', `alfred-dot-${statusState}`]"></span>
				<span class="alfred-status-text">{{ statusText }}</span>
				<span v-if="elapsedTime" class="alfred-elapsed-time text-muted text-xs">({{ elapsedTime }}s)</span>
				<span v-if="pipelineMode === 'lite'" class="alfred-mode-badge alfred-mode-lite" :title="liteBadgeTooltip">
					{{ __("Basic") }}
				</span>
			</div>
			<PhasePipeline
				v-if="pipelineMode !== 'lite'"
				:current-phase="currentPhase"
				:completed-phases="completedPhases"
				:active-agent="activeAgent"
			/>
		</div>

		<div
			ref="panelsEl"
			class="alfred-panels"
		>
			<!-- Left Panel -->
			<div class="alfred-left-panel">
				<!-- Conversation List -->
				<ConversationList
					v-if="!currentConversation"
					:conversations="conversations"
					@select="openConversation"
					@new-conversation="newConversation"
					@new-with-prompt="newConversationWithPrompt"
					@delete="deleteConversationFromList"
					@share="shareConversation"
				/>

				<!-- Chat Area -->
				<div v-else class="alfred-chat-area">
					<!-- Chat Toolbar: three-zone layout.
					     Zone 1 (left):  back + conversation title
					     Zone 2 (mid):   mode switcher (the identity of the current turn)
					     Zone 3 (right): primary "+ New" pill + single overflow menu
					     Secondary actions (Health, Share, Delete) live inside the
					     overflow menu so the bar never overflows regardless of how
					     narrow the chat area gets. This replaces the old row of
					     four side-by-side buttons that got clipped by the preview
					     panel. -->
					<div class="alfred-chat-toolbar">
						<div class="alfred-toolbar-left">
							<button
								type="button"
								class="alfred-icon-btn"
								@click="goBack"
								:aria-label="__('Back to conversations')"
								:title="__('Back to conversations')"
							>
								<span class="alfred-btn-glyph" aria-hidden="true">&#8592;</span>
							</button>
						</div>

						<div class="alfred-toolbar-center">
							<ModeSwitcher v-model="currentMode" />
						</div>

						<div class="alfred-toolbar-right">
							<button
								type="button"
								class="alfred-primary-btn"
								@click="newConversationFromChat"
								:aria-label="__('Start a new conversation')"
								:title="__('Start a new conversation')"
							>
								<span class="alfred-btn-glyph" aria-hidden="true">+</span>
								<span>{{ __("New") }}</span>
							</button>

							<!-- Overflow menu: single kebab button gates the three
							     less-frequent actions. Click toggles menuOpen; a
							     document-level listener (see onMounted) closes on
							     outside click. The menu is absolutely positioned
							     and right-aligned so it never clips against the
							     preview panel edge. -->
							<div class="alfred-menu-wrapper" ref="menuWrapperEl">
								<button
									type="button"
									class="alfred-icon-btn"
									:class="{ 'alfred-icon-btn-pressed': menuOpen }"
									@click.stop="menuOpen = !menuOpen"
									:aria-label="__('More actions')"
									:title="__('More actions')"
									:aria-expanded="menuOpen"
									aria-haspopup="menu"
								>
									<span class="alfred-btn-glyph" aria-hidden="true">&#8942;</span>
								</button>
								<div v-if="menuOpen" class="alfred-menu-dropdown" role="menu">
									<button
										type="button"
										class="alfred-menu-item"
										role="menuitem"
										@click="menuAction(checkHealth)"
									>
										<span class="alfred-menu-item-icon" aria-hidden="true">&#9829;</span>
										<span class="alfred-menu-item-label">{{ __("Check health") }}</span>
										<span class="alfred-menu-item-hint">{{ __("Redis, worker, app") }}</span>
									</button>
									<button
										v-if="isCurrentConvOwner"
										type="button"
										class="alfred-menu-item"
										role="menuitem"
										@click="menuAction(() => shareConversation(currentConversation))"
									>
										<span class="alfred-menu-item-icon" aria-hidden="true">&#8599;</span>
										<span class="alfred-menu-item-label">{{ __("Share conversation") }}</span>
									</button>
									<div v-if="isCurrentConvOwner" class="alfred-menu-separator" role="separator"></div>
									<button
										v-if="isCurrentConvOwner"
										type="button"
										class="alfred-menu-item alfred-menu-item-danger"
										role="menuitem"
										@click="menuAction(deleteConversation)"
									>
										<span class="alfred-menu-item-icon" aria-hidden="true">&#10005;</span>
										<span class="alfred-menu-item-label">{{ __("Delete conversation") }}</span>
									</button>
								</div>
							</div>
						</div>
					</div>
					<div ref="messagesContainer" class="alfred-messages">
						<!-- Empty-state welcome shown on a fresh conversation with
						     no messages yet. Hides the moment the first user
						     prompt or system row lands in messages[]. -->
						<div
							v-if="!messages.length && !isProcessing"
							class="alfred-empty-state"
						>
							<div class="alfred-empty-hero">
								<div class="alfred-empty-mark" aria-hidden="true">A</div>
								<h3 class="alfred-empty-title">{{ emptyGreeting }}</h3>
								<p class="alfred-empty-subtitle">{{ emptySubtitle }}</p>
							</div>
							<div class="alfred-empty-prompts">
								<div class="alfred-empty-prompts-label">
									{{ __("Try one of these") }}
								</div>
								<button
									v-for="(prompt, i) in emptyPrompts"
									:key="i"
									type="button"
									class="alfred-empty-prompt"
									@click="sendMessage(prompt)"
								>
									<span class="alfred-empty-prompt-icon" aria-hidden="true">&rsaquo;</span>
									<span class="alfred-empty-prompt-text">{{ prompt }}</span>
								</button>
							</div>
						</div>
						<MessageBubble
							v-for="msg in messages"
							:key="msg.name || msg._id"
							:message="msg"
							@option-click="sendMessage"
							@retry="retryLastMessage"
							@plan-refine="onPlanRefine"
							@plan-approve="onPlanApprove"
						/>
						<TypingIndicator v-if="isProcessing && !currentActivity" />
					</div>

					<!-- Activity Log (collapsible) -->
					<div v-if="activityLog.length" class="alfred-activity-log">
						<div class="alfred-activity-log-toggle" @click="activityLogOpen = !activityLogOpen">
							<span :class="['alfred-conn-dot', `alfred-dot-${connectionState}`]"></span>
							<span class="text-xs">{{ connectionLabel }}</span>
							<span class="text-muted text-xs" style="margin-left: auto;">
								{{ activityLogOpen ? '&#9662;' : '&#9656;' }} {{ __("Activity") }} ({{ activityLog.length }})
							</span>
						</div>
						<div v-if="activityLogOpen" class="alfred-activity-log-entries">
							<div
								v-for="(entry, idx) in activityLog"
								:key="idx"
								:class="['alfred-activity-entry', `alfred-activity-${entry.level}`]"
							>
								<span class="alfred-activity-time text-muted">{{ entry.time }}</span>
								<span>{{ entry.text }}</span>
							</div>
						</div>
					</div>

					<!-- Live activity ticker - shows what the agent is doing right now -->
					<div v-if="isProcessing && currentActivity" class="alfred-activity-ticker" :key="currentActivity">
						<span class="alfred-activity-ticker-icon">&#9679;</span>
						<span class="alfred-activity-ticker-text">{{ currentActivity }}</span>
					</div>

					<div class="alfred-input-area">
						<div class="alfred-input-row">
							<textarea
								ref="inputField"
								v-model="inputText"
								:placeholder="inputPlaceholder"
								:disabled="inputDisabled"
								rows="2"
								class="alfred-input"
								@keydown.enter.exact.prevent="sendMessage(inputText)"
							></textarea>
							<button
								v-if="isProcessing"
								class="btn btn-default btn-sm alfred-stop-btn"
								:disabled="cancelInFlight"
								:title="__('Stop the running agent gracefully; the current phase will finish.')"
								@click="cancelRun"
							>
								{{ cancelInFlight ? __("Stopping...") : __("Stop") }}
							</button>
							<button
								v-else
								class="btn btn-primary btn-sm alfred-send-btn"
								:disabled="inputDisabled || !inputText.trim()"
								@click="sendMessage(inputText)"
							>
								{{ __("Send") }}
							</button>
						</div>
						<span class="alfred-input-hint text-muted text-xs">
							{{ __("Enter to send, Shift+Enter for new line") }}
						</span>
					</div>
				</div>
			</div>

			<!-- Splitter: drag to resize the left / right split. Writes to
			     --alfred-left-width on .alfred-panels and persists the
			     latest value to localStorage. Hidden on mobile. -->
			<div
				class="alfred-splitter"
				:class="{ 'alfred-splitter-active': splitterDragging }"
				role="separator"
				aria-orientation="vertical"
				:aria-label="__('Resize chat and preview panels')"
				@mousedown.prevent="startSplitterDrag"
			></div>

			<!-- Right Panel: Preview -->
			<div class="alfred-right-panel">
				<PreviewPanel
					:changeset="changeset"
					:current-phase="currentPhase"
					:deploy-steps="deploySteps"
					:deployed="isDeployed"
					:is-processing="isProcessing"
					:conversation-status="conversationStatus"
					:validating="validatingChangeset"
					:rollback-in-flight="rollbackInFlight"
					@approve="approveChangeset"
					@modify="startModify"
					@reject="rejectChangeset"
					@rollback="rollbackChangeset"
				/>
			</div>
		</div>
	</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from "vue";
import ConversationList from "./ConversationList.vue";
import MessageBubble from "./MessageBubble.vue";
import TypingIndicator from "./TypingIndicator.vue";
import PhasePipeline from "./PhasePipeline.vue";
import PreviewPanel from "./PreviewPanel.vue";
import ModeSwitcher from "./ModeSwitcher.vue";

// ── State ──────────────────────────────────────────────────────
const conversations = ref([]);
const currentConversation = ref(null);
const messages = ref([]);
const inputText = ref("");
const inputPlaceholder = ref(__("Describe what you want to build..."));
const isProcessing = ref(false);
const inputDisabled = ref(false);
const statusText = ref(__("Ready"));
// Tracks an in-flight cancel request so repeated Stop clicks are debounced
// and the UI reflects that the cancel was accepted.
const cancelInFlight = ref(false);
// The Alfred Conversation.status string, rehydrated by openConversation /
// rehydrateConversationState. Drives the preview-panel CANCELLED /
// Completed / Failed empty-state copy without the parent having to rerun
// its own state derivation.
const conversationStatus = ref("");
// True while Approve has fired and the second-pass dry-run is running,
// so PreviewPanel flips to VALIDATING state and disables action buttons.
const validatingChangeset = ref(false);
// True while a user-initiated Rollback call is in flight. PreviewPanel
// shows "Rolling back..." on the button and disables it.
const rollbackInFlight = ref(false);
// The draggable splitter between left (chat) and right (preview) panels.
// splitterDragging drives a hover-style highlight on the handle while the
// user is actively dragging. panelsEl is the .alfred-panels container -
// drag math reads its bounding rect to convert the mouse X into a percentage.
const panelsEl = ref(null);
const splitterDragging = ref(false);
const SPLITTER_LS_KEY = "alfred_chat_left_width";
const SPLITTER_MIN_PX = 320;
const SPLITTER_MIN_RIGHT_PX = 360;
const statusState = ref("disconnected");
const currentPhase = ref(null);
const completedPhases = ref([]);
// Name of the agent currently running the active phase (e.g.
// "Developer"). Populated from agent_status events, cleared when
// the phase completes or a new prompt starts. The PhasePipeline
// renders this inside the active pill so the header reads
// "[pulse] Developer - generating code" instead of a static label.
const activeAgent = ref(null);
const changeset = ref(null);
const deploySteps = ref([]);
const isDeployed = ref(false);
const elapsedTime = ref(null);

// Three-mode chat (Phase D): user's per-conversation mode preference.
// "auto" lets the orchestrator decide; "dev"/"plan"/"insights" force a
// specific mode. Persisted on Alfred Conversation.mode via
// set_conversation_mode, reloaded on conversation select.
const currentMode = ref("auto");

const activityLog = ref([]);
const activityLogOpen = ref(false);
const connectionState = ref("disconnected"); // disconnected, starting, connected, reconnecting, failed

// Live "what is the agent doing right now" ticker - updated on each MCP tool call.
// Shown prominently above the input while isProcessing is true so the user never
// stares at a blank screen while the pipeline is running.
const currentActivity = ref(null);

// Pipeline mode: "full" (6-agent SDLC) or "lite" (single-agent fast pass).
// Sourced from the backend's first agent_status event each run, which reflects
// the highest-precedence config: admin portal plan > Alfred Settings > default.
const pipelineMode = ref("full");
// Where the mode came from: "plan" means the subscription tier forced it;
// "site_config" means the site admin chose it. Used in the tooltip so the user
// understands whether they can change it.
const pipelineModeSource = ref("site_config");

const messagesContainer = ref(null);
const inputField = ref(null);

// Overflow menu ("⋯" in the toolbar) - holds Health / Share / Delete.
// Collapsing these into a single dropdown is what keeps the toolbar
// from overflowing into the preview panel on narrow chat areas, so
// the menuOpen/close plumbing is load-bearing UI, not a nicety.
const menuOpen = ref(false);
const menuWrapperEl = ref(null);

let timerInterval = null;
let timerStart = null;
let realtimeBound = false;
let pollInterval = null;
let stuckTimeout = null;
// Timestamp of the most recent prompt - polling rejects any changeset created
// before this so we never show stale previews from an earlier prompt in the
// same conversation.
let currentPromptSentAt = null;

// Hard stop for processing UI state. If no completion / error event arrives
// within this window, surface a "pipeline stalled" warning so the user can
// retry instead of staring at a spinner forever.
const MAX_PROCESSING_MS = 10 * 60 * 1000;  // 10 minutes
// Cap on in-memory activity log entries (prevents unbounded growth over long sessions).
const MAX_ACTIVITY_LOG = 200;

// ── Computed ───────────────────────────────────────────────────
const lastUserMessage = computed(() => {
	const userMsgs = messages.value.filter((m) => m.role === "user");
	return userMsgs.length ? userMsgs[userMsgs.length - 1].content : "";
});

const CONNECTION_LABELS = {
	disconnected: __("Disconnected"),
	starting: __("Starting..."),
	connected: __("Connected"),
	reconnecting: __("Reconnecting..."),
	failed: __("Connection Failed"),
	stopped: __("Stopped"),
};
const connectionLabel = computed(() => CONNECTION_LABELS[connectionState.value] || connectionState.value);

const liteBadgeTooltip = computed(() => {
	if (pipelineModeSource.value === "plan") {
		return __("Basic mode is set by your subscription plan. Single-agent fast pipeline - ~5× faster, best for simple customizations. Upgrade your plan to unlock the full 6-agent pipeline.");
	}
	return __("Basic mode is configured in Alfred Settings. Single-agent fast pipeline - ~5× faster, best for simple customizations. Switch to Full in Alfred Settings for complex workflows.");
});

// Empty-state welcome content. Re-evaluated when the user flips the mode
// switcher so the greeting and starter prompts match the chosen lane.
const EMPTY_STARTERS = {
	auto: [
		__("Add a 'Priority' custom field on Task with High, Medium, Low"),
		__("Show top 10 customers by revenue this quarter"),
		__("Outline an approval workflow for Material Request"),
		__("Notify sales team when a Lead has no activity for 7 days"),
	],
	dev: [
		__("Add a 'Delivery Priority' custom field on Sales Order"),
		__("Create a notification when a Purchase Order exceeds 100,000"),
		__("Restrict Salary Structure read access to HR Manager only"),
		__("Add a print format for Sales Invoice with our company logo"),
	],
	plan: [
		__("Plan an approval workflow for expense claims over 50,000"),
		__("Draft a 'Vendor Onboarding' module with fields and permissions"),
		__("Outline a migration to add multi-warehouse support to Stock Entry"),
		__("Design a weekly sales digest for regional managers"),
	],
	insights: [
		__("Show top 10 customers by revenue this quarter"),
		__("Compare this month's sales to the same month last year"),
		__("What's our outstanding AR by age bucket?"),
		__("Which items had stock-outs in the last 30 days?"),
	],
};
const EMPTY_SUBTITLES = {
	auto: __("Ask me to build, plan, or analyze. I'll pick the right path."),
	dev: __("Tell me what to build or change, and I'll ship a reviewable changeset."),
	plan: __("Describe what you want, and I'll draft a plan before touching code."),
	insights: __("Ask a question about your data, and I'll run the numbers."),
};
const emptyGreeting = computed(() => {
	const name = (frappe.boot && frappe.boot.user && frappe.boot.user.first_name) || "";
	return name ? __("Hi {0}, I'm Alfred.", [name]) : __("Hi, I'm Alfred.");
});
const emptySubtitle = computed(
	() => EMPTY_SUBTITLES[currentMode.value] || EMPTY_SUBTITLES.auto,
);
const emptyPrompts = computed(
	() => EMPTY_STARTERS[currentMode.value] || EMPTY_STARTERS.auto,
);

function addActivity(text, level = "info") {
	const now = new Date();
	const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
	activityLog.value.push({ text, level, time });
	// Drop the oldest in bulk once we exceed the cap - splice is O(n) per call
	// but far cheaper than repeated shift() when the array grows quickly.
	if (activityLog.value.length > MAX_ACTIVITY_LOG) {
		activityLog.value.splice(0, activityLog.value.length - MAX_ACTIVITY_LOG);
	}
}

// ── Route Sync ────────────────────────────────────────────────
function getConversationFromRoute() {
	const route = frappe.get_route();
	// Route format: ["alfred-chat", conversation_id]
	return (route && route.length > 1 && route[1]) ? route[1] : null;
}

function syncRoute() {
	const convId = getConversationFromRoute();
	if (convId && convId !== currentConversation.value) {
		openConversation(convId);
	} else if (!convId && currentConversation.value) {
		// URL was cleared (e.g., browser back button) - go to list
		currentConversation.value = null;
		loadConversations();
	}
}

// ── Lifecycle ──────────────────────────────────────────────────
function handleDocumentClick(e) {
	if (!menuOpen.value) return;
	const wrapper = menuWrapperEl.value;
	if (wrapper && !wrapper.contains(e.target)) menuOpen.value = false;
}

function handleDocumentKey(e) {
	if (e.key === "Escape" && menuOpen.value) menuOpen.value = false;
}

function menuAction(fn) {
	menuOpen.value = false;
	fn();
}

onMounted(() => {
	loadConversations();
	setupRealtime();
	document.addEventListener("click", handleDocumentClick);
	document.addEventListener("keydown", handleDocumentKey);
	restoreSplitterWidth();
	// Restore conversation from URL on page load / refresh
	const convId = getConversationFromRoute();
	if (convId) {
		openConversation(convId);
	}
});

onUnmounted(() => {
	stopTimer();
	stopPolling();
	clearDisconnectWatchdog();
	document.removeEventListener("click", handleDocumentClick);
	document.removeEventListener("keydown", handleDocumentKey);
	document.removeEventListener("mousemove", handleSplitterMove);
	document.removeEventListener("mouseup", endSplitterDrag);
	// Listeners persist on frappe.realtime - they're global and idempotent
});

// ── Splitter drag handlers ─────────────────────────────────────
// Drag math: convert the mouse X into a pixel offset from the left edge
// of .alfred-panels, clamp between SPLITTER_MIN_PX and (panels_width -
// SPLITTER_MIN_RIGHT_PX), write back as a px value on --alfred-left-width.
// On mouseup, persist to localStorage so the same split restores next load.
function restoreSplitterWidth() {
	try {
		const stored = localStorage.getItem(SPLITTER_LS_KEY);
		if (stored && panelsEl.value) {
			panelsEl.value.style.setProperty("--alfred-left-width", stored);
		}
	} catch (e) { /* localStorage may be unavailable in sandbox */ }
}

function startSplitterDrag(evt) {
	if (!panelsEl.value) return;
	splitterDragging.value = true;
	document.body.style.userSelect = "none";
	document.body.style.cursor = "col-resize";
	document.addEventListener("mousemove", handleSplitterMove);
	document.addEventListener("mouseup", endSplitterDrag);
}

function handleSplitterMove(evt) {
	if (!splitterDragging.value || !panelsEl.value) return;
	const rect = panelsEl.value.getBoundingClientRect();
	let leftPx = evt.clientX - rect.left;
	const maxLeft = rect.width - SPLITTER_MIN_RIGHT_PX;
	if (leftPx < SPLITTER_MIN_PX) leftPx = SPLITTER_MIN_PX;
	if (leftPx > maxLeft) leftPx = maxLeft;
	panelsEl.value.style.setProperty("--alfred-left-width", `${leftPx}px`);
}

function endSplitterDrag() {
	if (!splitterDragging.value) return;
	splitterDragging.value = false;
	document.body.style.userSelect = "";
	document.body.style.cursor = "";
	document.removeEventListener("mousemove", handleSplitterMove);
	document.removeEventListener("mouseup", endSplitterDrag);
	try {
		const width = panelsEl.value?.style.getPropertyValue("--alfred-left-width");
		if (width) localStorage.setItem(SPLITTER_LS_KEY, width);
	} catch (e) { /* ignore */ }
}

// Auto-scroll when messages change
watch(messages, () => nextTick(scrollToBottom), { deep: true });

// ── API Calls ──────────────────────────────────────────────────
function loadConversations() {
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversations",
		callback: (r) => { if (r.message) conversations.value = r.message; },
	});
}

function newConversation() {
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.create_conversation",
		callback: (r) => {
			if (r.message) {
				openConversation(r.message.name);
				loadConversations();
			}
		},
	});
}

function newConversationWithPrompt(prompt) {
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.create_conversation",
		callback: (r) => {
			if (r.message) {
				openConversation(r.message.name);
				nextTick(() => {
					inputText.value = prompt;
					sendMessage(prompt);
				});
			}
		},
	});
}

function openConversation(name) {
	currentConversation.value = name;
	messages.value = [];
	changeset.value = null;
	deploySteps.value = [];
	isDeployed.value = false;
	currentPhase.value = null;
	completedPhases.value = [];
	activeAgent.value = null;
	activityLog.value = [];
	connectionState.value = "disconnected";
	statusText.value = __("Ready");
	statusState.value = "disconnected";
	conversationStatus.value = "";
	validatingChangeset.value = false;
	rollbackInFlight.value = false;
	clearDisconnectWatchdog();

	// Three-mode chat (Phase D): restore the sticky mode for this
	// conversation from the server-side record so navigating away and
	// back preserves the user's pick.
	const conv = conversations.value.find(c => c.name === name);
	const savedMode = ((conv?.mode) || "Auto").toLowerCase();
	currentMode.value = ["auto", "dev", "plan", "insights"].includes(savedMode)
		? savedMode
		: "auto";

	// Update URL so refresh preserves the open conversation
	if (getConversationFromRoute() !== name) {
		frappe.set_route("alfred-chat", name);
	}

	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_messages",
		args: { conversation: name },
		callback: (r) => { if (r.message) messages.value = r.message; },
	});

	// Rehydrate live state: pending changeset, active phase, processing flag.
	// Without this the UI forgets everything on a mid-run refresh even though
	// the pipeline is still running in the background.
	rehydrateConversationState(name);

	// Eagerly ensure the connection manager background job is running. This
	// is idempotent server-side, so calling on every open is safe and it
	// prevents "disconnected forever" state when a user opens an old
	// conversation without sending a new message (the old model only started
	// the manager inside send_message).
	ensureConnectionManager(name);
}

function ensureConnectionManager(name) {
	if (!name) return;
	frappe.call({
		method: "alfred_client.api.websocket_client.start_conversation",
		args: { conversation_name: name },
		callback: (r) => {
			const status = r?.message?.status;
			if (status === "no_worker") {
				frappe.show_alert({
					message: r.message.message || __(
						"No background worker is running - contact your admin.",
					),
					indicator: "red",
				});
			}
		},
		error: () => { /* non-fatal; watchdog will nag if it stays disconnected */ },
	});
}

// ── Disconnect watchdog ────────────────────────────────────────
// If the WS stays disconnected for more than 15 seconds while a
// conversation is open, try to restart the connection manager. The
// server side is idempotent, so the retry is a safe no-op if a job
// already came back up in the meantime. Auto-arms on every disconnect
// event from setupRealtime and clears when the connection is healthy.
let disconnectWatchdogTimer = null;

function armDisconnectWatchdog() {
	clearDisconnectWatchdog();
	if (!currentConversation.value) return;
	disconnectWatchdogTimer = setTimeout(() => {
		// Only nag if still disconnected at fire time and a conversation
		// is still open. Otherwise silently drop.
		if (!currentConversation.value) return;
		if (connectionState.value === "connected") return;
		addActivity(__("Connection still down after 15s; attempting restart..."), "error");
		ensureConnectionManager(currentConversation.value);
	}, 15000);
}

function clearDisconnectWatchdog() {
	if (disconnectWatchdogTimer) {
		clearTimeout(disconnectWatchdogTimer);
		disconnectWatchdogTimer = null;
	}
}

function rehydrateConversationState(name) {
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversation_state",
		args: { conversation: name },
		callback: (r) => {
			const state = r && r.message;
			if (!state || currentConversation.value !== name) return;

			// Conversation status drives the EMPTY-state copy in the preview
			// panel ("Run cancelled", "Conversation complete", etc.).
			conversationStatus.value = state.status || "";

			// Pipeline mode from the most recent run. Without this, the phase
			// pipeline would default to "full" after a refresh during a lite
			// run and show the wrong number of phases.
			if (state.pipeline_mode === "full" || state.pipeline_mode === "lite") {
				pipelineMode.value = state.pipeline_mode;
			}

			// Ticker text - lost today on every refresh because agent_activity
			// events are realtime-only; Phase 1 started caching it on the
			// Conversation row so this rehydrate can fill the ticker immediately.
			if (state.current_activity) currentActivity.value = state.current_activity;

			// Preview panel: pick the one variant that is populated. Priority
			// pending > deployed > failed so an in-flight review always wins
			// over a historical deploy.
			if (state.pending_changeset) {
				changeset.value = state.pending_changeset;
				isDeployed.value = false;
			} else if (state.deployed_changeset) {
				changeset.value = state.deployed_changeset;
				isDeployed.value = true;
			} else if (state.failed_changeset) {
				changeset.value = state.failed_changeset;
				isDeployed.value = false;
			}

			if (state.is_processing) {
				isProcessing.value = true;
				inputDisabled.value = true;
				statusState.value = "processing";
				statusText.value = state.active_agent
					? __("{0} is working...", [state.active_agent])
					: __("Pipeline running...");
				if (state.active_phase) currentPhase.value = state.active_phase;
				if (state.active_agent) activeAgent.value = state.active_agent;
				if (Array.isArray(state.completed_phases)) {
					completedPhases.value = state.completed_phases.slice();
				}
				// Kick the timer and polling fallback so the resumed run stays
				// observable even if no fresh realtime events have landed yet.
				startTimer();
				startPolling();
			}
		},
	});
}

function rollbackChangeset() {
	// Triggered by the Rollback button on the DEPLOYED preview panel.
	// Unlike the automatic rollback on deploy failure, this is a user
	// action on a successfully-deployed changeset.
	const cs = changeset.value;
	if (!cs || !cs.name) return;
	if (rollbackInFlight.value) return;
	frappe.confirm(
		__("Rollback deploys all removed-record data back to Alfred but will DELETE every document that was created. Continue?"),
		() => {
			rollbackInFlight.value = true;
			addActivity(__("Rolling back deploy..."));
			frappe.call({
				method: "alfred_client.api.deploy.rollback_changeset",
				args: { changeset_name: cs.name },
				callback: (r) => {
					rollbackInFlight.value = false;
					const result = (r && r.message) || {};
					const status = result.status || "";
					if (status === "Rolled Back") {
						changeset.value = {
							...cs,
							status: "Rolled Back",
							deployment_log: result.deployment_log || cs.deployment_log,
						};
						isDeployed.value = false;
						conversationStatus.value = "Completed";
						addActivity(__("Rollback complete."));
						messages.value.push({
							_id: Date.now(),
							role: "system",
							message_type: "status",
							content: __("Deploy rolled back."),
						});
					} else {
						addActivity(__("Rollback reported status: {0}", [status || "unknown"]), "error");
					}
				},
				error: (err) => {
					rollbackInFlight.value = false;
					addActivity(__("Rollback failed"), "error");
					console.warn("Rollback failed:", err);
				},
			});
		},
	);
}

// Three-mode chat (Phase D): persist the user's mode pick to the
// conversation whenever it changes. Watched with a short debounce
// (immediate: false) so the initial load from openConversation doesn't
// trigger a write back to the server.
watch(currentMode, (next, prev) => {
	if (!currentConversation.value) return;
	if (prev === undefined) return;  // initial assignment on load - skip
	const frappeValue = next.charAt(0).toUpperCase() + next.slice(1);
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.set_conversation_mode",
		args: { conversation: currentConversation.value, mode: frappeValue },
		error: (err) => {
			console.warn("Failed to save chat mode:", err);
		},
	});
});

const conversationSummary = computed(() => {
	const conv = conversations.value.find(c => c.name === currentConversation.value);
	return conv?.first_message || currentConversation.value || "";
});

const isCurrentConvOwner = computed(() => {
	const conv = conversations.value.find(c => c.name === currentConversation.value);
	return conv ? conv.is_owner : true;
});

function goBack() {
	currentConversation.value = null;
	stopTimer();
	frappe.set_route("alfred-chat");
	loadConversations();
}

function newConversationFromChat() {
	goBack();
	newConversation();
}

function deleteConversation() {
	if (!currentConversation.value) return;
	confirmAndDelete(currentConversation.value, () => goBack());
}

function checkHealth() {
	if (!currentConversation.value) return;
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversation_health",
		args: { conversation: currentConversation.value },
		callback: (r) => {
			if (!r.message) return;
			const h = r.message;
			const esc = frappe.utils.escape_html;

			const lastMsg = h.last_message
				? `${esc(h.last_message.role)} (${esc(h.last_message.message_type)}) - ${esc(h.last_message.creation)}`
				: '<span class="text-muted">-</span>';

			const procStatus = h.processing_app_reachable
				? '<span style="color: var(--green-600); font-weight: 600;">&#10003; reachable</span>'
				: `<span style="color: var(--red-600); font-weight: 600;">&#10007; ${esc(h.processing_app_error || "unreachable")}</span>`;

			const jobStatus = h.background_job_running
				? '<span style="color: var(--green-600); font-weight: 600;">&#10003; running</span>'
				: '<span style="color: var(--red-600); font-weight: 600;">&#10007; not running</span>';

			const workerCount = Number.isFinite(h.long_worker_count) ? h.long_worker_count : -1;
			let workerStatus;
			if (workerCount === -1) {
				workerStatus = '<span class="text-muted">-</span>';
			} else if (workerCount === 0) {
				workerStatus = `<span style="color: var(--red-600); font-weight: 600;">&#10007; 0 workers</span>
					<span class="text-muted" style="margin-left: 8px;">${__("worker_long not running - check Procfile + bench restart")}</span>`;
			} else {
				workerStatus = `<span style="color: var(--green-600); font-weight: 600;">&#10003; ${workerCount} worker(s)</span>`;
			}

			const depth = h.redis_queue_depth || 0;
			const queueColor = depth === 0 ? "var(--green-600)" : "var(--orange-600)";
			const queueLabel = depth === 0
				? __("empty (drained or never had a message)")
				: __("{0} message(s) waiting", [depth]);

			const overallOk = h.processing_app_reachable && h.background_job_running && workerCount > 0;

			frappe.msgprint({
				title: __("Conversation Health"),
				indicator: overallOk ? "green" : "orange",
				message: `
					<table class="table table-bordered" style="margin: 0;">
						<tbody>
							<tr>
								<td style="width: 40%;"><strong>${__("Conversation Status")}</strong></td>
								<td>${esc(h.conversation_status || "-")}</td>
							</tr>
							<tr>
								<td><strong>${__("Current Agent")}</strong></td>
								<td>${esc(h.current_agent || "-")}</td>
							</tr>
							<tr>
								<td><strong>${__("Last Message")}</strong></td>
								<td>${lastMsg}</td>
							</tr>
							<tr>
								<td><strong>${__("Long-Queue Workers")}</strong></td>
								<td>${workerStatus}</td>
							</tr>
							<tr>
								<td><strong>${__("Background Job")}</strong></td>
								<td>${jobStatus}</td>
							</tr>
							<tr>
								<td><strong>${__("Redis Queue Depth")}</strong></td>
								<td>
									<span style="color: ${queueColor}; font-weight: 600;">${depth}</span>
									<span class="text-muted" style="margin-left: 8px;">${queueLabel}</span>
								</td>
							</tr>
							<tr>
								<td><strong>${__("Processing App")}</strong></td>
								<td>${procStatus}</td>
							</tr>
						</tbody>
					</table>
					<p class="text-muted text-xs" style="margin-top: 10px; margin-bottom: 0;">
						${__("Tip: send a prompt and click Health immediately. Queue depth should briefly show 1, then drop to 0 within 1-2 seconds as the connection manager drains it.")}
					</p>
				`,
			});
		},
		error: () => {
			frappe.show_alert({ message: __("Failed to fetch health"), indicator: "red" });
		},
	});
}

function deleteConversationFromList(name) {
	confirmAndDelete(name, () => loadConversations());
}

function confirmAndDelete(conversation, onSuccess) {
	frappe.confirm(
		__("Delete this conversation and all its messages? This cannot be undone."),
		() => {
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.delete_conversation",
				args: { conversation },
				callback: () => {
					frappe.show_alert({ message: __("Conversation deleted"), indicator: "green" });
					onSuccess();
				},
				error: () => {
					frappe.show_alert({ message: __("Failed to delete conversation"), indicator: "red" });
				},
			});
		}
	);
}

function shareConversation(name) {
	const d = new frappe.ui.Dialog({
		title: __("Share Conversation"),
		fields: [
			{
				fieldname: "user",
				fieldtype: "Link",
				options: "User",
				label: __("User"),
				reqd: 1,
				filters: { enabled: 1, name: ["!=", frappe.session.user] },
			},
			{
				fieldname: "write",
				fieldtype: "Check",
				label: __("Allow writing (send messages)"),
				default: 0,
			},
		],
		primary_action_label: __("Share"),
		primary_action(values) {
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.share_conversation",
				args: { conversation: name, user: values.user, write: values.write },
				callback: () => {
					frappe.show_alert({ message: __("Conversation shared with {0}", [values.user]), indicator: "green" });
					d.hide();
				},
				error: () => {
					frappe.show_alert({ message: __("Failed to share conversation"), indicator: "red" });
				},
			});
		},
	});
	d.show();
}

// Three-mode chat: `mode` is optional. When omitted, the user's current
// UI mode selection (currentMode - see ModeSwitcher) is used. When
// passed explicitly (e.g. from Plan-doc "Approve and Build" button),
// that mode overrides the UI selection for this one turn.
function sendMessage(text, modeOverride) {
	const msg = typeof text === "string" ? text.trim() : inputText.value.trim();
	if (!msg || !currentConversation.value) return;

	inputText.value = "";

	// Record when this prompt was sent so the polling fallback can reject
	// changesets that belong to an earlier prompt in the same conversation.
	currentPromptSentAt = frappe.datetime.now_datetime();

	// Clear any previous changeset so the preview panel doesn't show a stale
	// result while the new pipeline is running.
	changeset.value = null;

	// Optimistic UI
	messages.value.push({
		_id: Date.now(),
		role: "user",
		message_type: "text",
		content: msg,
		creation: frappe.datetime.now_datetime(),
	});

	isProcessing.value = true;
	inputDisabled.value = true;
	statusText.value = __("Processing...");
	statusState.value = "processing";
	addActivity("Message sent, waiting for agent pipeline...");
	startTimer();
	startPolling(); // Poll for changeset as fallback if realtime events don't arrive

	const effectiveMode = (modeOverride || currentMode.value || "auto").toLowerCase();

	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message",
		args: {
			conversation: currentConversation.value,
			message: msg,
			mode: effectiveMode,
		},
		error: () => {
			isProcessing.value = false;
			inputDisabled.value = false;
			statusText.value = __("Error sending message");
			statusState.value = "error";
			stopTimer();
			stopPolling();
		},
	});
}

function retryLastMessage() {
	if (lastUserMessage.value) sendMessage(lastUserMessage.value);
}

function cancelRun() {
	// Graceful cancel: the processing app lets the current phase finish,
	// exits the pipeline cleanly, and emits `alfred_run_cancelled`. We
	// optimistically drop isProcessing so the Stop button disappears right
	// away; the realtime handler reconciles the chat transcript.
	if (!currentConversation.value || cancelInFlight.value) return;
	cancelInFlight.value = true;
	addActivity(__("Cancelling run..."));
	statusText.value = __("Cancelling...");
	statusState.value = "processing";
	frappe.call({
		method: "alfred_client.api.websocket_client.cancel_run",
		args: { conversation_name: currentConversation.value },
		callback: () => {
			// Give the WS path ~3s to land `alfred_run_cancelled`; if it
			// doesn't, fall back to local state so the UI never stays stuck.
			setTimeout(() => {
				if (!isProcessing.value) return;
				isProcessing.value = false;
				inputDisabled.value = false;
				stopTimer();
				stopPolling();
				currentActivity.value = null;
				statusText.value = __("Cancelled");
				statusState.value = "success";
				messages.value.push({
					_id: Date.now(),
					role: "system",
					message_type: "status",
					content: __("Run cancelled."),
				});
				cancelInFlight.value = false;
			}, 3000);
		},
		error: () => {
			cancelInFlight.value = false;
			addActivity(__("Failed to send cancel request"), "error");
		},
	});
}

function approveChangeset() {
	if (!changeset.value) return;
	const changes = changeset.value.changes || [];

	frappe.confirm(
		`<p><strong>${__("Deploy to your live site?")}</strong></p>
		 <p class="text-muted">${__("A dry-run validation will be performed first. Changes:")}</p>
		 <ul style="text-align:left">${changes.map((c) =>
			`<li><strong>${c.op || c.operation || "create"}</strong> ${c.doctype}: ${frappe.utils.escape_html((c.data || {}).name || "Unnamed")}</li>`
		 ).join("")}</ul>`,
		() => {
			validatingChangeset.value = true;
			frappe.show_alert({ message: __("Validating changeset..."), indicator: "blue" });
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.approve_changeset",
				args: { changeset_name: changeset.value.name },
				callback: (r) => {
					validatingChangeset.value = false;
					if (!r.message) return;
					let result = r.message;

					if (result.status === "validation_failed") {
						// Dry-run failed - show issues without deploying
						let issueHtml = (result.issues || []).map((i) =>
							`<li><strong>[${i.severity}]</strong> Step ${i.step} (${i.doctype}): ${frappe.utils.escape_html(i.issue)}</li>`
						).join("");
						frappe.msgprint({
							title: __("Dry-Run Validation Failed"),
							indicator: "red",
							message: `<p>${result.message}</p><ul>${issueHtml}</ul>
								<p class="text-muted">${__("No changes were made to your site. Fix the issues and try again.")}</p>`,
						});
						messages.value.push({
							_id: Date.now(), role: "system", message_type: "error",
							content: `Deployment validation failed: ${result.message}`,
						});
					} else if (result.status === "success") {
						isDeployed.value = true;
						if (changeset.value) changeset.value.status = "Deployed";
						frappe.show_alert({ message: __("Deployment complete!"), indicator: "green" });
					} else if (result.status === "failed") {
						if (changeset.value) changeset.value.status = "Rolled Back";
						frappe.msgprint({
							title: __("Deployment Failed"),
							indicator: "red",
							message: result.error || "An error occurred during deployment.",
						});
					}
				},
				error: () => {
					validatingChangeset.value = false;
					frappe.show_alert({ message: __("Deployment failed."), indicator: "red" });
				},
			});
		}
	);
}

function startModify() {
	inputDisabled.value = false;
	inputPlaceholder.value = __("What would you like to change?");
	nextTick(() => inputField.value?.focus());
}

function rejectChangeset() {
	if (!changeset.value) return;
	frappe.confirm(__("Are you sure you want to reject this changeset?"), () => {
		frappe.call({
			method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.reject_changeset",
			args: { changeset_name: changeset.value.name },
			callback: () => {
				if (changeset.value) changeset.value.status = "Rejected";
				frappe.show_alert({ message: __("Changeset rejected."), indicator: "orange" });
			},
		});
	});
}

// ── Real-time Events ───────────────────────────────────────────
function setupRealtime() {
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
				if (currentPromptSentAt && cs.creation && cs.creation < currentPromptSentAt) {
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

// Phase C plan action handlers: Refine drops the suggested text into the
// input so the user can edit it before sending. Approve & Build sends
// the canned approval prompt with mode=dev so the backend flips the
// plan to approved and the Dev crew picks it up as a spec.
function onPlanRefine(message) {
	const plan = message?.plan;
	const title = plan?.title || "";
	const suggestion = title
		? `Refine the plan '${title}': `
		: "Refine the plan: ";
	inputText.value = suggestion;
	// Focus the input so the user can keep typing immediately.
	try {
		const ta = document.querySelector(".alfred-chat-input textarea");
		if (ta) ta.focus();
	} catch (e) { /* ignore */ }
}

function onPlanApprove(message) {
	const plan = message?.plan;
	const title = plan?.title || "";
	const canned = title
		? `Approve and build the plan: ${title}`
		: "Approve and build the plan";
	sendMessage(canned, "dev");
}

// ── Agent Status / Phase Pipeline ──────────────────────────────
const AGENT_PHASE_MAP = {
	"Requirement Analyst": "requirement", requirement: "requirement",
	"Feasibility Assessor": "assessment", assessment: "assessment",
	"Solution Architect": "architecture", architect: "architecture",
	"Frappe Developer": "development", developer: "development",
	"QA Validator": "testing", tester: "testing",
	"Deployment Specialist": "deployment", deployer: "deployment",
};

function updateAgentStatus(data) {
	const phase = AGENT_PHASE_MAP[data.agent] || null;
	if (data.status === "started" || data.status === "running") {
		const step = phase ? Object.keys(AGENT_PHASE_MAP).indexOf(phase) / 2 + 1 : "";
		statusText.value = step ? `Step ${Math.ceil(step)}/6 - ${data.agent} is working...` : `${data.agent} is working...`;
		statusState.value = "processing";
		if (phase) currentPhase.value = phase;
		activeAgent.value = data.agent || null;
		startTimer();
	} else if (data.status === "completed") {
		statusText.value = `${data.agent} completed`;
		statusState.value = "success";
		if (phase && !completedPhases.value.includes(phase)) {
			completedPhases.value.push(phase);
		}
		currentPhase.value = null;
		activeAgent.value = null;
	}
}

// ── Agent Step Messages (live feed in chat) ──────────────────
const STEP_LABELS = {
	requirement: "Gathering requirements",
	assessment: "Checking feasibility",
	architecture: "Designing solution",
	development: "Generating code",
	testing: "Validating changeset",
	deployment: "Preparing deployment",
};

function agentStepLabel(phaseOrAgent) {
	return STEP_LABELS[phaseOrAgent] || phaseOrAgent;
}

function pushAgentStep(text) {
	messages.value.push({
		_id: `step-${Date.now()}`,
		role: "system",
		message_type: "agent-step",
		step_status: "active",
		content: text,
	});
	nextTick(scrollToBottom);
}

function markLastStepDone() {
	for (let i = messages.value.length - 1; i >= 0; i--) {
		if (messages.value[i].message_type === "agent-step" && messages.value[i].step_status === "active") {
			messages.value[i].step_status = "done";
			break;
		}
	}
}

// ── Timer ──────────────────────────────────────────────────────
function startTimer() {
	timerStart = Date.now();
	stopTimer();
	timerInterval = setInterval(() => {
		elapsedTime.value = Math.round((Date.now() - timerStart) / 1000);
	}, 1000);
	// Guard against the pipeline getting stuck without sending completion
	// or error events (processing-app crash, network drop, etc.)
	stuckTimeout = setTimeout(() => {
		if (isProcessing.value) {
			isProcessing.value = false;
			inputDisabled.value = false;
			statusText.value = __("Pipeline appears stalled");
			statusState.value = "error";
			currentActivity.value = null;
			stopPolling();
			addActivity("Pipeline stalled - no response for 10 minutes", "error");
			messages.value.push({
				_id: Date.now(), role: "system", message_type: "error",
				content: __("The pipeline hasn't responded for 10 minutes. You can try again or check the processing app logs."),
			});
		}
	}, MAX_PROCESSING_MS);
}

function stopTimer() {
	if (timerInterval) clearInterval(timerInterval);
	timerInterval = null;
	elapsedTime.value = null;
	if (stuckTimeout) { clearTimeout(stuckTimeout); stuckTimeout = null; }
}

// ── Polling (fallback for when realtime events don't arrive) ──
function startPolling() {
	stopPolling();
	pollInterval = setInterval(() => {
		if (!currentConversation.value || !isProcessing.value) {
			stopPolling();
			return;
		}
		pollForChangeset();
	}, 5000); // Check every 5 seconds
}

function stopPolling() {
	if (pollInterval) clearInterval(pollInterval);
	pollInterval = null;
}

function pollForChangeset() {
	if (!currentConversation.value) return;
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_latest_changeset",
		args: { conversation: currentConversation.value },
		async: true,
		callback: (r) => {
			if (!r.message) return;
			let cs = r.message;
			if (!cs.name) return;

			// Reject changesets from a previous prompt in the same conversation.
			// Without this, the first poll after sending a new prompt can latch
			// onto the previous changeset and falsely declare the new pipeline done.
			if (currentPromptSentAt && cs.creation && cs.creation < currentPromptSentAt) {
				return;
			}

			// Already have this changeset - nothing to do
			if (changeset.value && changeset.value.name === cs.name) return;

			changeset.value = cs;
			isProcessing.value = false;
			currentActivity.value = null;
			stopTimer();
			stopPolling();
			inputDisabled.value = false;
			inputPlaceholder.value = __("Ask a follow-up or start a new request...");
			statusText.value = __("Review the proposed changes");
			statusState.value = "success";
			addActivity("Changeset ready for review");
			messages.value.push({
				_id: Date.now(), role: "assistant", message_type: "text",
				content: __("I've prepared the changes for your review. Please check the preview panel on the right."),
			});
			nextTick(scrollToBottom);
		},
	});
}

// ── Scroll ─────────────────────────────────────────────────────
function scrollToBottom() {
	const el = messagesContainer.value;
	if (el) el.scrollTop = el.scrollHeight;
}

// Expose goBack for the page shell
defineExpose({ goBack, currentConversation, syncRoute });
</script>

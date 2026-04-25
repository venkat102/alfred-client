<template>
	<div class="alfred-app" :class="{ 'alfred-app--drawer-open': drawerOpen && currentConversation }">
		<!-- Flow Strip retired: the agent state now lives in the floating
		     status pill inside .alfred-transcript (see AgentStatusPill). -->

		<!-- Conversation List: shown when no conversation is open. No
		     topbar, no drawer, no composer - just the sidebar hero. -->
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
					<!-- Topbar: frosted, 48px sticky header. Three zones -
					     left (back + mark + title), center (mode switcher),
					     right (+ New + kebab overflow). No status here;
					     the floating status pill inside .alfred-transcript
					     carries that job. -->
					<div class="alfred-topbar">
						<div class="alfred-topbar-zone alfred-topbar-zone--left">
							<button
								type="button"
								class="alfred-icon-btn"
								@click="goBack"
								:aria-label="__('Back to conversations')"
								:title="__('Back to conversations')"
							>
								<span class="alfred-btn-glyph" aria-hidden="true">&#8592;</span>
							</button>
							<div class="alfred-brand">
								<div class="alfred-mark alfred-mark--chat alfred-mark--sm" aria-hidden="true">A</div>
								<h1 class="alfred-topbar-title" :title="conversationSummary">{{ conversationSummary }}</h1>
							</div>
						</div>

						<div class="alfred-topbar-zone alfred-topbar-zone--center">
							<ModeSwitcher v-model="currentMode" />
						</div>

						<div class="alfred-topbar-zone alfred-topbar-zone--right">
							<button
								type="button"
								class="alfred-primary-btn alfred-primary-btn--gradient"
								@click="newConversationFromChat"
								:aria-label="__('Start a new conversation')"
								:title="__('Start a new conversation')"
							>
								<span class="alfred-btn-glyph" aria-hidden="true">+</span>
								<span>{{ __("New") }}</span>
							</button>

							<button
								type="button"
								class="alfred-icon-btn alfred-topbar-preview-toggle"
								:class="{
									'alfred-icon-btn-pressed': drawerOpen,
									'alfred-topbar-preview-toggle--unseen': unseenChanges,
								}"
								:aria-label="drawerOpen ? __('Close preview') : __('Open preview')"
								:title="drawerOpen ? __('Close preview') : __('Open preview')"
								:aria-expanded="drawerOpen"
								aria-controls="alfred-drawer-title"
								@click="toggleDrawer"
							>
								<span class="alfred-btn-glyph" aria-hidden="true">&#9776;</span>
							</button>
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
										<span class="alfred-menu-item-icon alfred-menu-item-icon--info" aria-hidden="true">&#9829;</span>
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
										<span class="alfred-menu-item-icon alfred-menu-item-icon--neutral" aria-hidden="true">&#8599;</span>
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
										<span class="alfred-menu-item-icon alfred-menu-item-icon--danger" aria-hidden="true">&#10005;</span>
										<span class="alfred-menu-item-label">{{ __("Delete conversation") }}</span>
									</button>
								</div>
							</div>
						</div>
					</div>

					<!-- Transcript: floating status pill on top, scroll
					     container holding messages, composer absolute at
					     bottom. Pill sits absolute at top-center so the
					     user always sees current agent state. -->
					<div class="alfred-transcript">
						<div
							ref="pillWrapperEl"
							:class="['alfred-status-pill-anchor', { 'alfred-status-pill-anchor--scrolled': transcriptScrolled }]"
						>
							<AgentStatusPill
								:state="statusPillState"
								:agent-name="activeAgent"
								:activity="currentActivity"
								:elapsed="elapsedTime"
								:pipeline-mode="pipelineMode"
								:current-phase="currentPhase"
								:completed-phases="completedPhases"
								:label="statusText"
								:open="pillPopoverOpen"
								@update:open="pillPopoverOpen = $event"
							/>
						</div>

						<div ref="messagesContainer" class="alfred-transcript-scroll">
							<div class="alfred-messages">
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
									@save-as-report="onSaveAsReport"
								/>
								<TypingIndicator v-if="isProcessing && !currentActivity" />
							</div>
						</div>

						<!-- Composer wrap: centered, floating at bottom of the
						     transcript area. Saturation banner and activity
						     log live here too so they follow the composer
						     instead of stealing scroll space. -->
						<div class="alfred-composer-wrap">
							<div
								v-if="saturationBanner"
								:class="['alfred-saturation-banner', `alfred-saturation-${saturationBanner.tone}`]"
								role="button"
								:tabindex="0"
								:title="__('Click to open the Health dialog')"
								@click="checkHealth"
								@keydown.enter.space.prevent="checkHealth"
							>
								<span class="alfred-saturation-icon" aria-hidden="true">&#9888;</span>
								<span class="alfred-saturation-text">{{ saturationBanner.text }}</span>
							</div>

							<div v-if="activityLog.length" class="alfred-activity-log">
								<div class="alfred-activity-log-toggle" @click="activityLogOpen = !activityLogOpen">
									<span :class="['alfred-conn-dot', `alfred-dot-${connectionState}`]"></span>
									<span class="alfred-eyebrow">{{ connectionLabel }}</span>
									<span class="alfred-activity-log-count">
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

							<div class="alfred-composer">
								<textarea
									ref="inputField"
									v-model="inputText"
									:placeholder="inputPlaceholder"
									:disabled="inputDisabled"
									rows="2"
									class="alfred-composer-input"
									data-testid="alfred-composer-input"
									@keydown.enter.exact.prevent="sendMessage(inputText)"
									@keydown.meta.enter.exact.prevent="sendMessage(inputText)"
									@keydown.ctrl.enter.exact.prevent="sendMessage(inputText)"
								></textarea>
								<div class="alfred-composer-actions">
									<div class="alfred-composer-kbd-hints">
										<kbd class="alfred-kbd">Enter</kbd>
										<span>{{ __("to send") }}</span>
										<kbd class="alfred-kbd">Shift</kbd>
										<span>+</span>
										<kbd class="alfred-kbd">Enter</kbd>
										<span>{{ __("for newline") }}</span>
									</div>
									<button
										v-if="isProcessing"
										class="alfred-btn-ghost alfred-btn-ghost--danger alfred-stop-btn"
										data-testid="alfred-stop-btn"
										:disabled="cancelInFlight"
										:title="__('Stop the running agent gracefully; the current phase will finish.')"
										@click="cancelRun"
									>
										<span v-if="cancelInFlight" class="alfred-btn-spinner" aria-hidden="true"></span>
										<span v-else class="alfred-stop-glyph" aria-hidden="true">&#9632;</span>
										<span>{{ cancelInFlight ? __("Stopping...") : __("Stop") }}</span>
									</button>
									<button
										v-else
										class="alfred-btn-primary alfred-send-btn"
										data-testid="alfred-send-btn"
										:disabled="inputDisabled || !inputText.trim()"
										@click="sendMessage(inputText)"
									>
										<span>{{ __("Send") }}</span>
										<span class="alfred-send-glyph" aria-hidden="true">&rarr;</span>
									</button>
								</div>
							</div>
						</div>
					</div>
				</div>

		<!-- Preview drawer: slide-in overlay on the right. Only renders
		     when a conversation is open. Auto-opens when a non-empty
		     changeset exists (see autoOpenOnChangeset watcher). -->
		<PreviewDrawer
			v-if="currentConversation"
			:model-value="drawerOpen"
			:changeset="changeset"
			:current-phase="currentPhase"
			:deploy-steps="deploySteps"
			:deployed="isDeployed"
			:is-processing="isProcessing"
			:conversation-status="conversationStatus"
			:validating="validatingChangeset"
			:rollback-in-flight="rollbackInFlight"
			@update:model-value="setDrawerOpen($event)"
			@minimize="minimizeDrawer"
			@approve="approveChangeset"
			@modify="startModify"
			@reject="rejectChangeset"
			@rollback="rollbackChangeset"
		/>

		<!-- Floating pill: when drawer is closed AND a changeset exists,
		     show a tiny "Preview: N changes" chip at bottom-right that
		     reopens the drawer. -->
		<button
			v-if="currentConversation && !drawerOpen && previewChangeCount > 0"
			type="button"
			class="alfred-preview-minimized-pill"
			:class="{ 'alfred-preview-minimized-pill--unseen': unseenChanges }"
			@click="openDrawer"
			:aria-label="__('Reopen preview drawer')"
		>
			<span class="alfred-mark alfred-mark--preview alfred-mark--sm" aria-hidden="true">&#9670;</span>
			<span class="alfred-preview-minimized-pill-label">
				{{ __("Preview") }}
			</span>
			<span class="alfred-chip alfred-chip--neutral alfred-preview-minimized-pill-count">
				{{ previewChangeCount }}
			</span>
		</button>
	</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from "vue";
import ConversationList from "./ConversationList.vue";
import MessageBubble from "./MessageBubble.vue";
import TypingIndicator from "./TypingIndicator.vue";
import AgentStatusPill from "./AgentStatusPill.vue";
import PreviewDrawer from "./PreviewDrawer.vue";
import ModeSwitcher from "./ModeSwitcher.vue";
import { useDrawerState } from "./composables/useDrawerState";
import { useConversationAdmin } from "./composables/useConversationAdmin";
import { usePreviewActions } from "./composables/usePreviewActions";
import { useAlfredRealtime } from "./composables/useAlfredRealtime";

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

// Preview drawer state. Owned by useDrawerState - persists to localStorage,
// toggles the body `alfred-drawer-open` class for page-level CSS. previewChangeCount
// (computed below from changeset.value?.changes) drives the minimized-pill badge.
const {
	drawerOpen,
	unseenChanges,
	setDrawerOpen,
	openDrawer,
	closeDrawer,
	toggleDrawer,
	minimizeDrawer,
} = useDrawerState();

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

// Count of changes in the current changeset, used by the minimized pill
// badge and to gate whether the pill renders at all. Defensive: changes
// can be an array (already parsed) or a JSON string (raw from the wire).
const previewChangeCount = computed(() => {
	const raw = changeset.value?.changes;
	if (!raw) return 0;
	try {
		const arr = typeof raw === "string" ? JSON.parse(raw) : raw;
		return Array.isArray(arr) ? arr.length : 0;
	} catch { return 0; }
});

// Auto-open drawer when a meaningful changeset arrives while the drawer
// is closed, so the user sees the review panel without extra clicks. We
// only trigger on the edge from "no changeset" to "has changeset" to
// avoid reopening the drawer every time the user manually closes it
// during an active changeset's lifecycle.
watch(previewChangeCount, (now, was) => {
	if (now > 0 && (was === 0 || was === undefined)) {
		if (!drawerOpen.value) {
			unseenChanges.value = true;
			if (currentConversation.value) {
				setDrawerOpen(true);
			}
		}
	}
	if (now === 0) {
		// Changeset cleared (new prompt, rejection resolved, etc.).
		// Drop the unseen flag so a future changeset can set it again.
		unseenChanges.value = false;
	}
});

// Three-mode chat (Phase D): user's per-conversation mode preference.
// "auto" lets the orchestrator decide; "dev"/"plan"/"insights" force a
// specific mode. Persisted on Alfred Conversation.mode via
// set_conversation_mode, reloaded on conversation select.
const currentMode = ref("auto");

const activityLog = ref([]);
const activityLogOpen = ref(false);
const connectionState = ref("disconnected"); // disconnected, starting, connected, reconnecting, failed

// Queue-saturation banner state. Shown above the input when either
// start_conversation returned no_worker or the manager has been
// enqueued without connecting for SATURATION_WATCHDOG_MS. The
// watchdog helpers + saturationBanner computed live further down next
// to armSaturationWatchdog / clearSaturationWatchdog.
const saturationReason = ref(null); // null | "waiting" | "no_worker"

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

// Floating agent status pill state. statusPillState is computed from the
// processing lifecycle plus a short outcome window: when a run ends we
// flash the pill for OUTCOME_FADE_MS so the user sees the final state
// before it settles back to idle. pillPopoverOpen drives the click-to
// expand popover that reveals the six-step pipeline.
const OUTCOME_FADE_MS = 4000;
const outcomeFlash = ref(null); // "success" | "error" | null
let outcomeFadeTimer = null;
const pillPopoverOpen = ref(false);
const pillWrapperEl = ref(null);

// True once the transcript has been scrolled down a few pixels. Drives a
// condensed-pill style (hides the live activity phrase, keeps agent +
// elapsed) so the floating chip feels less noisy over scrolled content.
const transcriptScrolled = ref(false);
let onTranscriptScroll = null;

const statusPillState = computed(() => {
	if (isProcessing.value) return "processing";
	if (outcomeFlash.value === "success") return "outcome-success";
	if (outcomeFlash.value === "error") return "outcome-error";
	return "idle";
});

// Watch isProcessing edges to flash an outcome on completion. We only
// set the flash when dropping out of processing so a fresh page load
// into the idle state does not trigger a phantom "Completed" pill.
watch(isProcessing, (now, was) => {
	if (was && !now) {
		// Determine outcome from the final status state. "error" stays
		// longer (5s) because errors are higher-signal than completions.
		const stateAtEnd = statusState.value;
		if (stateAtEnd === "error") {
			outcomeFlash.value = "error";
		} else {
			outcomeFlash.value = "success";
		}
		if (outcomeFadeTimer) clearTimeout(outcomeFadeTimer);
		outcomeFadeTimer = setTimeout(() => {
			outcomeFlash.value = null;
			outcomeFadeTimer = null;
		}, OUTCOME_FADE_MS);
	}
	if (!was && now) {
		// Starting a new run: clear any lingering flash and close the
		// popover so the first paint shows a fresh processing pill.
		outcomeFlash.value = null;
		if (outcomeFadeTimer) {
			clearTimeout(outcomeFadeTimer);
			outcomeFadeTimer = null;
		}
	}
});

let timerInterval = null;
let timerStart = null;
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
		// URL was cleared (e.g., browser back button) - go to list. Same
		// reset contract as goBack so the list view never inherits a
		// running pipeline's typing/status state.
		resetChatViewState();
		currentConversation.value = null;
		loadConversations();
	}
}

// ── Lifecycle ──────────────────────────────────────────────────
function handleDocumentClick(e) {
	// Close overflow menu on outside click
	if (menuOpen.value) {
		const menuWrap = menuWrapperEl.value;
		if (menuWrap && !menuWrap.contains(e.target)) menuOpen.value = false;
	}
	// Close pill popover on outside click
	if (pillPopoverOpen.value) {
		const pillWrap = pillWrapperEl.value;
		if (pillWrap && !pillWrap.contains(e.target)) pillPopoverOpen.value = false;
	}
}

function handleDocumentKey(e) {
	if (e.key !== "Escape") return;
	// Precedence: menu -> pill popover -> drawer. Only close one layer
	// per press so the user can escape nested surfaces step by step.
	if (menuOpen.value) {
		menuOpen.value = false;
		return;
	}
	if (pillPopoverOpen.value) {
		pillPopoverOpen.value = false;
		return;
	}
	if (drawerOpen.value) {
		closeDrawer();
	}
}

function menuAction(fn) {
	menuOpen.value = false;
	fn();
}

onMounted(() => {
	loadConversations();
	setupAlfredRealtime();
	document.addEventListener("click", handleDocumentClick);
	document.addEventListener("keydown", handleDocumentKey);
	// Mark the body while the chat page is mounted so our scoped CSS can
	// hide Frappe's empty .navbar-breadcrumbs strip. Purely additive:
	// removed on unmount so every other Desk page is unaffected.
	document.body.classList.add("alfred-page-active");
	// Restore the drawer-open body class if localStorage says so. The
	// drawer itself suppresses the slide animation on initial paint via
	// a `ready` flag inside PreviewDrawer.vue.
	if (drawerOpen.value) document.body.classList.add("alfred-drawer-open");
	// Restore conversation from URL on page load / refresh
	const convId = getConversationFromRoute();
	if (convId) {
		openConversation(convId);
	}
	// Condensed-pill trigger: watch .alfred-transcript-scroll and flip a
	// boolean once the user scrolls a few pixels. The ref is only
	// populated after a conversation is opened (v-else branch), so we
	// wait a tick and re-attach whenever the scroll container remounts.
	nextTick(() => attachTranscriptScrollListener());
});

function attachTranscriptScrollListener() {
	const el = messagesContainer.value;
	if (!el || onTranscriptScroll) return;
	onTranscriptScroll = () => {
		transcriptScrolled.value = el.scrollTop > 8;
	};
	el.addEventListener("scroll", onTranscriptScroll, { passive: true });
}

function detachTranscriptScrollListener() {
	const el = messagesContainer.value;
	if (el && onTranscriptScroll) {
		el.removeEventListener("scroll", onTranscriptScroll);
	}
	onTranscriptScroll = null;
	transcriptScrolled.value = false;
}

// Re-attach the listener whenever the scroll container remounts (e.g.,
// switching between ConversationList and an open conversation). Vue
// reuses the ref slot, so watching messagesContainer covers the mount
// transition cleanly.
watch(messagesContainer, (el) => {
	detachTranscriptScrollListener();
	if (el) nextTick(() => attachTranscriptScrollListener());
});

onUnmounted(() => {
	stopTimer();
	stopPolling();
	clearDisconnectWatchdog();
	if (outcomeFadeTimer) {
		clearTimeout(outcomeFadeTimer);
		outcomeFadeTimer = null;
	}
	detachTranscriptScrollListener();
	document.removeEventListener("click", handleDocumentClick);
	document.removeEventListener("keydown", handleDocumentKey);
	document.body.classList.remove("alfred-page-active");
	document.body.classList.remove("alfred-drawer-open");
	// Listeners persist on frappe.realtime - they're global and idempotent
});

// ── Splitter drag handlers ─────────────────────────────────────
// Splitter retired with the conversation-first shell. The legacy
// localStorage key "alfred_chat_left_width" is no longer read or
// written; stale values linger harmlessly until the browser clears
// them on its own schedule.

// Auto-scroll when messages change
watch(messages, () => nextTick(scrollToBottom), { deep: true });

// ── API Calls ──────────────────────────────────────────────────
// Conversation-admin helpers (load / delete / share / health) live in
// useConversationAdmin so this file is free of boilerplate frappe.call
// glue. The composable takes only the two refs it needs (conversations,
// currentConversation) and returns plain functions the template + the
// other functions in this file can call directly.
const {
	loadConversations,
	confirmAndDelete,
	deleteConversationFromList,
	shareConversation,
	checkHealth,
} = useConversationAdmin({ conversations, currentConversation });

// Preview-panel actions (approve / modify / reject / rollback). Receives
// every ref they touch and the two non-ref helpers (addActivity + nextTick)
// so the composable stays independent of this SFC's imports. All four are
// user-initiated from PreviewDrawer's buttons.
const {
	approveChangeset,
	startModify,
	rejectChangeset,
	rollbackChangeset,
} = usePreviewActions({
	changeset,
	validatingChangeset,
	rollbackInFlight,
	isDeployed,
	conversationStatus,
	messages,
	inputDisabled,
	inputPlaceholder,
	inputField,
	addActivity,
	nextTick,
});

// The 15 frappe.realtime.on handlers that bridge WebSocket events to
// chat state. The composable binds once and replaces this file's old
// setupRealtime() function. currentPromptSentAt is a plain `let`
// (mutated by sendMessage below), so it's passed as a getter rather
// than a ref.
const { setupAlfredRealtime } = useAlfredRealtime({
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
	getCurrentPromptSentAt: () => currentPromptSentAt,
});

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

// Centralized chat-view state reset. Called from every entry/exit path
// (openConversation, goBack, syncRoute) so neither path can leak the
// previous conversation's UI state into the next view.
//
// Why this exists: an earlier version reset state inline in
// openConversation but goBack only nulled currentConversation. Exiting a
// chat with an in-flight pipeline left isProcessing/currentActivity/etc.
// set, and the next chat (or a freshly created one) showed the typing
// indicator and agent status pill from the previous run until a hard
// refresh.
//
// Invariant: every ref that drives chat-shell UI (transcript, status pill,
// preview drawer, input controls, saturation banner) and every long-lived
// timer/watchdog is listed here. If you add a new chat-view ref, add it
// here too.
function resetChatViewState() {
	// Transcript + activity log
	messages.value = [];
	activityLog.value = [];
	activityLogOpen.value = false;

	// Status pill + activity ticker
	isProcessing.value = false;
	currentActivity.value = null;
	activeAgent.value = null;
	currentPhase.value = null;
	completedPhases.value = [];
	elapsedTime.value = null;
	statusText.value = __("Ready");
	statusState.value = "disconnected";
	connectionState.value = "disconnected";
	pipelineMode.value = "full";
	pipelineModeSource.value = "site_config";
	pillPopoverOpen.value = false;
	outcomeFlash.value = null;
	if (outcomeFadeTimer) {
		clearTimeout(outcomeFadeTimer);
		outcomeFadeTimer = null;
	}

	// Preview drawer + changeset
	changeset.value = null;
	deploySteps.value = [];
	isDeployed.value = false;
	conversationStatus.value = "";
	validatingChangeset.value = false;
	rollbackInFlight.value = false;

	// Input controls
	inputDisabled.value = false;
	cancelInFlight.value = false;

	// Saturation banner
	saturationReason.value = null;

	// Long-lived timers + watchdogs
	stopTimer();
	stopPolling();
	clearDisconnectWatchdog();
	clearSaturationWatchdog();
}

function openConversation(name) {
	resetChatViewState();
	currentConversation.value = name;

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

// Shape a Frappe RPC error payload into a single human-readable line.
// Frappe surfaces failures in three different shapes depending on what went
// wrong (server exception, validation throw, network drop, 403 from a stale
// CSRF token). The silent `error: () => {}` pattern on a frappe.call hides
// all of these, which is how a stuck pipeline ends up looking identical to
// "your session expired silently 6 minutes ago" - the bug we just hit.
//
// Always pass the error through this so the activity-log message names the
// real cause, not a generic "Error sending message".
function frappeCallErrorMessage(err) {
	if (!err) return __("Request failed (no details). Try refreshing the page.");
	// Frappe's _server_messages is a JSON-encoded list of {message, indicator,...}
	if (err._server_messages) {
		try {
			const parsed = JSON.parse(err._server_messages);
			const msgs = (Array.isArray(parsed) ? parsed : []).map((m) => {
				try { return JSON.parse(m).message; } catch { return m; }
			}).filter(Boolean);
			if (msgs.length) return msgs.join(" - ");
		} catch (_) { /* fall through */ }
	}
	if (err.exc_type) return `${err.exc_type}: ${err.message || err.exception || ""}`.trim();
	if (err.message) return err.message;
	if (err.statusText) return `HTTP ${err.status || "?"} ${err.statusText}`;
	if (typeof err === "string") return err;
	return __("Request failed. Try refreshing the page.");
}

function ensureConnectionManager(name) {
	if (!name) return;
	frappe.call({
		method: "alfred_client.api.websocket_client.start_conversation",
		args: { conversation_name: name },
		callback: (r) => {
			const status = r?.message?.status;
			if (status === "no_worker") {
				saturationReason.value = "no_worker";
				clearSaturationWatchdog();
				frappe.show_alert({
					message: r.message.message || __(
						"No background worker is running - contact your admin.",
					),
					indicator: "red",
				});
				return;
			}
			if (status === "already_running") {
				// Manager exists; if we are actually connected this will be
				// cleared by the realtime listener. Leave whatever banner
				// state we have alone.
				return;
			}
			if (status === "enqueued") {
				// Freshly enqueued. Arm a watchdog: if we do not reach
				// "connected" within SATURATION_WATCHDOG_MS, every worker is
				// probably busy with a long-lived manager for another
				// conversation, so warn the user rather than let them stare
				// at a silent UI.
				armSaturationWatchdog();
			}
		},
		error: (err) => {
			// Surface the actual reason. Common cause is a stale browser
			// session (403) - without this log entry the user just sees
			// "Connected" pulses and assumes the pipeline is running.
			addActivity(
				__("Could not start connection manager: {0}", [frappeCallErrorMessage(err)]),
				"error",
			);
		},
	});
}

// ── Queue saturation banner ───────────────────────────────────
// Shown above the input when either start_conversation returned
// no_worker or the manager has been enqueued without connecting for
// SATURATION_WATCHDOG_MS. The banner is informational: clicking it
// opens the Health dialog so the user can see worker count + queue
// depth themselves. The saturationReason ref itself lives with the
// other top-of-file state refs so composables can pass it in during
// setup without hitting a temporal-dead-zone error.
const SATURATION_WATCHDOG_MS = 10000;
let saturationWatchdogTimer = null;

function armSaturationWatchdog() {
	clearSaturationWatchdog();
	saturationWatchdogTimer = setTimeout(() => {
		// Only flag if we still haven't connected by fire time. If the
		// manager came up in the meantime, useAlfredRealtime's "connected"
		// listener will have already cleared the reason.
		if (connectionState.value === "connected") return;
		if (!currentConversation.value) return;
		saturationReason.value = "waiting";
	}, SATURATION_WATCHDOG_MS);
}

function clearSaturationWatchdog() {
	if (saturationWatchdogTimer) {
		clearTimeout(saturationWatchdogTimer);
		saturationWatchdogTimer = null;
	}
}

const saturationBanner = computed(() => {
	if (saturationReason.value === "no_worker") {
		return {
			tone: "red",
			text: __("No background worker is running. Open Health for details."),
		};
	}
	if (saturationReason.value === "waiting") {
		return {
			tone: "amber",
			text: __("Waiting for a worker - other conversations are holding the queue. Open Health for details."),
		};
	}
	return null;
});

// ── Disconnect watchdog ────────────────────────────────────────
// If the WS stays disconnected for more than 15 seconds while a
// conversation is open, try to restart the connection manager. The
// server side is idempotent, so the retry is a safe no-op if a job
// already came back up in the meantime. Auto-arms on every disconnect
// event from useAlfredRealtime and clears when the connection is healthy.
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
	resetChatViewState();
	currentConversation.value = null;
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

	// Capture the optimistic bubble we just pushed so the error path can
	// remove it - otherwise a silent send failure leaves the user's prompt
	// hanging in the transcript with no follow-up, which is exactly the
	// stuck-pipeline experience we are trying to eliminate.
	const optimisticBubble = messages.value[messages.value.length - 1];
	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message",
		args: {
			conversation: currentConversation.value,
			message: msg,
			mode: effectiveMode,
		},
		error: (err) => {
			isProcessing.value = false;
			inputDisabled.value = false;
			statusText.value = __("Error sending message");
			statusState.value = "error";
			stopTimer();
			stopPolling();
			// Drop the optimistic user bubble - the server didn't accept it,
			// so showing it as if it were sent is misleading.
			if (optimisticBubble && messages.value[messages.value.length - 1] === optimisticBubble) {
				messages.value.pop();
			}
			// Loud activity-log entry so the user can see WHY the send failed
			// (most often a stale browser session = HTTP 403). Pair it with a
			// clear next-step hint.
			addActivity(
				__("Send failed: {0}", [frappeCallErrorMessage(err)]),
				"error",
			);
			addActivity(
				__("If this keeps happening, refresh the page (Cmd+Shift+R) - your session may have expired."),
				"error",
			);
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
		error: (err) => {
			cancelInFlight.value = false;
			addActivity(
				__("Cancel request failed: {0}", [frappeCallErrorMessage(err)]),
				"error",
			);
		},
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

// V4 Insights -> Report handoff. User clicked "Save as Report" on an
// Insights reply. Fire a Dev-mode turn with a prompt that is
// human-readable at the top and carries the candidate as a machine-
// parseable __report_candidate__ JSON trailer. The pipeline's
// _parse_report_candidate_marker strips the trailer and short-circuits
// intent classification to create_report with source=handoff, so the
// Report Builder specialist runs against the already-resolved query
// shape.
function onSaveAsReport(candidate) {
	if (!candidate || typeof candidate !== "object") return;
	const lines = ["Save as Report:"];
	if (candidate.target_doctype) lines.push(`Source DocType: ${candidate.target_doctype}`);
	if (candidate.report_type) lines.push(`Report type: ${candidate.report_type}`);
	if (candidate.suggested_name) lines.push(`Suggested name: ${candidate.suggested_name}`);
	if (candidate.limit) lines.push(`Limit: ${candidate.limit}`);
	if (candidate.time_range) {
		const r = candidate.time_range;
		lines.push(`Time range: ${r.field || "date"} in ${r.preset || r.value || ""}`);
	}
	lines.push("");
	lines.push(`__report_candidate__: ${JSON.stringify(candidate)}`);
	sendMessage(lines.join("\n"), "dev");
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

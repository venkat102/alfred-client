<template>
	<div class="alfred-app">
		<!-- Status Bar -->
		<div class="alfred-status-bar">
			<div class="alfred-agent-status">
				<span :class="['alfred-status-dot', `alfred-dot-${statusState}`]"></span>
				<span class="alfred-status-text">{{ statusText }}</span>
				<span v-if="elapsedTime" class="alfred-elapsed-time text-muted text-xs">({{ elapsedTime }}s)</span>
			</div>
			<PhasePipeline :current-phase="currentPhase" :completed-phases="completedPhases" />
		</div>

		<div class="alfred-panels">
			<!-- Left Panel -->
			<div class="alfred-left-panel">
				<!-- Conversation List -->
				<ConversationList
					v-if="!currentConversation"
					:conversations="conversations"
					@select="openConversation"
					@new-conversation="newConversation"
					@new-with-prompt="newConversationWithPrompt"
				/>

				<!-- Chat Area -->
				<div v-else class="alfred-chat-area">
					<div ref="messagesContainer" class="alfred-messages">
						<MessageBubble
							v-for="msg in messages"
							:key="msg.name || msg._id"
							:message="msg"
							@option-click="sendMessage"
							@retry="retryLastMessage"
						/>
						<TypingIndicator v-if="isProcessing" />
					</div>

					<div class="alfred-input-area">
						<div class="alfred-input-wrapper">
							<textarea
								ref="inputField"
								v-model="inputText"
								:placeholder="inputPlaceholder"
								:disabled="inputDisabled"
								rows="2"
								class="alfred-input"
								@keydown.enter.exact.prevent="sendMessage(inputText)"
							></textarea>
							<span class="alfred-input-hint text-muted text-xs">
								{{ __("Enter to send, Shift+Enter for new line") }}
							</span>
						</div>
						<button
							class="btn btn-primary btn-sm alfred-send-btn"
							:disabled="inputDisabled || !inputText.trim()"
							@click="sendMessage(inputText)"
						>
							{{ __("Send") }}
						</button>
					</div>
				</div>
			</div>

			<!-- Right Panel: Preview -->
			<div class="alfred-right-panel">
				<PreviewPanel
					:changeset="changeset"
					:current-phase="currentPhase"
					:deploy-steps="deploySteps"
					:deployed="isDeployed"
					@approve="approveChangeset"
					@modify="startModify"
					@reject="rejectChangeset"
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

// ── State ──────────────────────────────────────────────────────
const conversations = ref([]);
const currentConversation = ref(null);
const messages = ref([]);
const inputText = ref("");
const inputPlaceholder = ref(__("Describe what you want to build..."));
const isProcessing = ref(false);
const inputDisabled = ref(false);
const statusText = ref(__("Ready"));
const statusState = ref("disconnected");
const currentPhase = ref(null);
const completedPhases = ref([]);
const changeset = ref(null);
const deploySteps = ref([]);
const isDeployed = ref(false);
const elapsedTime = ref(null);

const messagesContainer = ref(null);
const inputField = ref(null);

let timerInterval = null;
let timerStart = null;
let realtimeBound = false;

// ── Computed ───────────────────────────────────────────────────
const lastUserMessage = computed(() => {
	const userMsgs = messages.value.filter((m) => m.role === "user");
	return userMsgs.length ? userMsgs[userMsgs.length - 1].content : "";
});

// ── Lifecycle ──────────────────────────────────────────────────
onMounted(() => {
	loadConversations();
	setupRealtime();
});

onUnmounted(() => {
	stopTimer();
	// Listeners persist on frappe.realtime - they're global and idempotent
});

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
	statusText.value = __("Ready");
	statusState.value = "disconnected";

	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_messages",
		args: { conversation: name },
		callback: (r) => { if (r.message) messages.value = r.message; },
	});
}

function goBack() {
	currentConversation.value = null;
	stopTimer();
	loadConversations();
}

function sendMessage(text) {
	const msg = typeof text === "string" ? text.trim() : inputText.value.trim();
	if (!msg || !currentConversation.value) return;

	inputText.value = "";

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
	startTimer();

	frappe.call({
		method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message",
		args: { conversation: currentConversation.value, message: msg },
		error: () => {
			isProcessing.value = false;
			inputDisabled.value = false;
			statusText.value = __("Error sending message");
			statusState.value = "error";
			stopTimer();
		},
	});
}

function retryLastMessage() {
	if (lastUserMessage.value) sendMessage(lastUserMessage.value);
}

function approveChangeset() {
	if (!changeset.value) return;
	const changes = changeset.value.changes || [];
	const summary = changes
		.map((c) => `${c.op || c.operation || "create"} ${c.doctype}: ${(c.data || {}).name || "Unnamed"}`)
		.join("\n• ");

	frappe.confirm(
		`<p><strong>${__("Deploy to your live site?")}</strong></p>
		 <p class="text-muted">${__("The following changes will be applied:")}</p>
		 <ul style="text-align:left">${changes.map((c) =>
			`<li><strong>${c.op || c.operation}</strong> ${frappe.utils.escape_html((c.data || {}).name || "")}</li>`
		 ).join("")}</ul>`,
		() => {
			frappe.call({
				method: "alfred_client.alfred_settings.page.alfred_chat.alfred_chat.approve_changeset",
				args: { changeset_name: changeset.value.name },
				callback: (r) => {
					if (r.message) {
						isDeployed.value = true;
						frappe.show_alert({ message: __("Deployment complete!"), indicator: "green" });
					}
				},
				error: () => frappe.show_alert({ message: __("Deployment failed."), indicator: "red" }),
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
				changeset.value = null;
				frappe.show_alert({ message: __("Changeset rejected."), indicator: "orange" });
			},
		});
	});
}

// ── Real-time Events ───────────────────────────────────────────
function setupRealtime() {
	if (realtimeBound) return;
	realtimeBound = true;

	frappe.realtime.on("alfred_agent_status", (data) => {
		if (!currentConversation.value) return;
		isProcessing.value = false;
		updateAgentStatus(data);

		if (data.status === "completed" && data.agent) {
			messages.value.push({
				_id: Date.now(), role: "system", message_type: "status",
				content: `${data.agent} completed`,
			});
		}
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
			callback: (r) => { if (r.message) changeset.value = r.message; },
		});
	});

	frappe.realtime.on("alfred_error", (data) => {
		if (!currentConversation.value) return;
		isProcessing.value = false;
		inputDisabled.value = false;
		stopTimer();
		statusText.value = __("Error");
		statusState.value = "error";
		messages.value.push({
			_id: Date.now(), role: "system", message_type: "error",
			content: data.error || data.message || "An error occurred",
		});
	});

	frappe.realtime.on("alfred_deploy_progress", (data) => {
		if (!currentConversation.value) return;
		deploySteps.value = [...deploySteps.value.filter((s) => s.step !== data.step), data];
	});

	frappe.realtime.on("alfred_deploy_complete", (data) => {
		if (!currentConversation.value) return;
		stopTimer();
		isDeployed.value = true;
		inputDisabled.value = false;
		inputPlaceholder.value = __("Ask a follow-up or start a new request...");
		statusText.value = __("Deployment complete");
		statusState.value = "success";
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
		messages.value.push({
			_id: Date.now(), role: "system", message_type: "error",
			content: `Deployment failed at step ${data.step}: ${data.error}. All changes rolled back.`,
		});
	});
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
		startTimer();
	} else if (data.status === "completed") {
		statusText.value = `${data.agent} completed`;
		statusState.value = "success";
		if (phase && !completedPhases.value.includes(phase)) {
			completedPhases.value.push(phase);
		}
		currentPhase.value = null;
	}
}

// ── Timer ──────────────────────────────────────────────────────
function startTimer() {
	timerStart = Date.now();
	stopTimer();
	timerInterval = setInterval(() => {
		elapsedTime.value = Math.round((Date.now() - timerStart) / 1000);
	}, 1000);
}

function stopTimer() {
	if (timerInterval) clearInterval(timerInterval);
	timerInterval = null;
	elapsedTime.value = null;
}

// ── Scroll ─────────────────────────────────────────────────────
function scrollToBottom() {
	const el = messagesContainer.value;
	if (el) el.scrollTop = el.scrollHeight;
}

// Expose goBack for the page shell
defineExpose({ goBack, currentConversation });
</script>

<template>
	<div :class="['alfred-message', `alfred-msg-${message.role || 'system'}`, `alfred-msg-type-${message.message_type || 'text'}`]">
		<div v-if="message.role !== 'user'" class="alfred-msg-header">
			<span v-if="message.agent_name" class="alfred-agent-badge">{{ message.agent_name }}</span>
			<span v-if="modeBadge" :class="['alfred-mode-badge', `alfred-mode-${modeBadge.toLowerCase()}`]">{{ modeBadge }}</span>
			<span class="alfred-msg-time text-muted text-xs">{{ formattedTime }}</span>
		</div>
		<div class="alfred-msg-content">
			<!-- Question -->
			<div v-if="message.message_type === 'question'" class="alfred-question-card">
				<div class="alfred-question-icon">?</div>
				<div class="alfred-question-body">
					<div class="alfred-question-text">{{ message.content }}</div>
					<div v-if="options.length" class="alfred-question-options">
						<button
							v-for="opt in options"
							:key="opt"
							class="btn btn-xs btn-default alfred-option-btn"
							@click="$emit('option-click', opt)"
						>
							{{ opt }}
						</button>
					</div>
					<div class="alfred-question-waiting text-muted text-xs">
						{{ __("Alfred is waiting for your response") }}
					</div>
				</div>
			</div>

			<!-- Error -->
			<div v-else-if="message.message_type === 'error'" class="alfred-error-msg">
				<div class="alfred-error-user-msg">{{ friendlyError }}</div>
				<details v-if="technicalError" class="alfred-error-details">
					<summary class="text-xs text-muted">{{ __("Technical details") }}</summary>
					<pre class="text-xs">{{ technicalError }}</pre>
				</details>
				<button class="btn btn-xs btn-default" style="margin-top: 6px;" @click="$emit('retry')">
					{{ __("Retry") }}
				</button>
			</div>

			<!-- Agent step (live progress) -->
			<div v-else-if="message.message_type === 'agent-step'" class="alfred-agent-step-msg">
				<span class="alfred-step-icon">{{ message.step_status === 'done' ? '&#10003;' : '&#9679;' }}</span>
				<span :class="{ 'text-muted': message.step_status === 'done' }">{{ message.content }}</span>
			</div>

			<!-- Status -->
			<div v-else-if="message.message_type === 'status'" class="alfred-status-msg">
				{{ message.content }}
			</div>

			<!-- Mode switch notice (three-mode chat orchestrator decision) -->
			<div v-else-if="message.message_type === 'mode_switch'" class="alfred-mode-switch-msg text-xs text-muted">
				<span class="alfred-mode-switch-icon">&#8651;</span>
				{{ modeSwitchLabel }}
			</div>

			<!-- Chat reply (conversational mode, no crew) -->
			<div v-else-if="message.message_type === 'chat_reply'" class="alfred-chat-reply-msg" v-html="renderedContent"></div>

			<!-- Insights reply (read-only Q&A mode, markdown) -->
			<div v-else-if="message.message_type === 'insights_reply'" class="alfred-insights-reply-msg" v-html="renderedContent"></div>

			<!-- Plan doc (Phase C plan mode, structured panel) -->
			<PlanDocPanel
				v-else-if="message.message_type === 'plan_doc' && planData"
				:plan="planData"
				@refine="$emit('plan-refine', message)"
				@approve="$emit('plan-approve', message)"
			/>

			<!-- Default: text with markdown -->
			<div v-else class="alfred-text-msg" v-html="renderedContent"></div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";
import PlanDocPanel from "./PlanDocPanel.vue";

const props = defineProps({
	message: { type: Object, required: true },
});

defineEmits(["option-click", "retry", "plan-refine", "plan-approve"]);

const formattedTime = computed(() => {
	if (!props.message.creation) return "";
	const diff = (new Date() - new Date(props.message.creation)) / (1000 * 60 * 60);
	return diff < 24
		? frappe.datetime.prettyDate(props.message.creation)
		: frappe.datetime.str_to_user(props.message.creation);
});

const options = computed(() => {
	try {
		const meta = typeof props.message.metadata === "string"
			? JSON.parse(props.message.metadata)
			: props.message.metadata;
		return meta?.options || [];
	} catch { return []; }
});

// Three-mode chat: pull the mode out of metadata / explicit field so we
// can render a small badge next to agent messages. Returns one of
// "Dev" / "Plan" / "Insights" / "Chat" or an empty string (no badge).
const modeBadge = computed(() => {
	if (props.message.role === "user") return "";
	try {
		const meta = typeof props.message.metadata === "string"
			? JSON.parse(props.message.metadata)
			: props.message.metadata;
		const m = (props.message.mode || meta?.mode || "").toLowerCase();
		if (!m) return "";
		if (m === "dev") return "Dev";
		if (m === "plan") return "Plan";
		if (m === "insights") return "Insights";
		if (m === "chat") return "Chat";
		return "";
	} catch { return ""; }
});

// Three-mode chat (Phase C): extract the plan doc dict from a plan_doc
// message. The backend sends the plan in metadata.plan (via the
// connection manager's store helper); older messages may have the plan
// inline on the message object.
const planData = computed(() => {
	try {
		const meta = typeof props.message.metadata === "string"
			? JSON.parse(props.message.metadata)
			: props.message.metadata;
		return meta?.plan || props.message.plan || null;
	} catch { return null; }
});

// Three-mode chat: user-visible label for a mode_switch event. Falls back
// to a generic sentence if the reason/mode are missing.
const modeSwitchLabel = computed(() => {
	try {
		const meta = typeof props.message.metadata === "string"
			? JSON.parse(props.message.metadata)
			: props.message.metadata;
		const mode = (meta?.mode || props.message.mode || "").toLowerCase();
		const reason = meta?.reason || "";
		const pretty = mode ? mode.charAt(0).toUpperCase() + mode.slice(1) : "";
		if (pretty && reason) return `Switched to ${pretty} mode: ${reason}`;
		if (pretty) return `Switched to ${pretty} mode`;
		return props.message.content || "Mode changed";
	} catch { return props.message.content || "Mode changed"; }
});

// Error mapping
const ERROR_MAP = [
	[/ValidationError/i, "There was a problem with the data format. Alfred will try a different approach."],
	[/PermissionError/i, "You don't have permission for this operation. Contact your administrator."],
	[/DuplicateEntryError/i, "A document with this name already exists on your site."],
	[/TimedOut|timeout/i, "The operation took too long. Please try again."],
	[/ConnectionError|ECONNREFUSED/i, "Could not connect to the processing service. Please try again later."],
	[/PROMPT_BLOCKED/i, "Your message was flagged by the security filter. Please rephrase your request."],
];

const friendlyError = computed(() => {
	const raw = props.message.content || "";
	for (const [pattern, friendly] of ERROR_MAP) {
		if (pattern.test(raw)) return friendly;
	}
	return raw;
});

const technicalError = computed(() => {
	const raw = props.message.content || "";
	return friendlyError.value !== raw ? raw : "";
});

// Safe markdown rendering (input is escaped via v-html after sanitization)
const renderedContent = computed(() => {
	let text = frappe.utils.escape_html(props.message.content || "");
	text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => `<pre class="alfred-code-preview"><code>${code.trim()}</code></pre>`);
	text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
	text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
	text = text.replace(/__(.+?)__/g, "<strong>$1</strong>");
	text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");
	text = text.replace(/^[\-\*]\s+(.+)$/gm, "<li>$1</li>");
	text = text.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
	text = text.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");
	text = text.replace(/^###\s+(.+)$/gm, "<h6>$1</h6>");
	text = text.replace(/^##\s+(.+)$/gm, "<h5>$1</h5>");
	text = text.replace(/^#\s+(.+)$/gm, "<h4>$1</h4>");
	text = text.replace(/\n/g, "<br>");
	text = text.replace(/<\/(pre|ul|li|h[456])><br>/g, "</$1>");
	return text;
});
</script>

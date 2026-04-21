<template>
	<div class="alfred-conversation-list">
		<!-- Empty state: no conversations yet. Mirrors the chat-side
		     welcome language (mark + hero title + hints + starter
		     prompts + primary action). -->
		<div v-if="!conversations.length" class="alfred-conv-empty-state">
			<div class="alfred-conv-empty-hero">
				<div class="alfred-mark alfred-mark--chat" aria-hidden="true">A</div>
				<h3 class="alfred-conv-empty-title">{{ __("Welcome to Alfred") }}</h3>
				<p class="alfred-conv-empty-subtitle">
					{{ __("I build Frappe customizations through conversation. Tell me what you need and I'll prepare a reviewable changeset.") }}
				</p>
			</div>
			<div class="alfred-conv-empty-hints">
				<div class="alfred-eyebrow alfred-conv-empty-hints-label">{{ __("How it works") }}</div>
				<div class="alfred-card alfred-conv-empty-hint">
					<span class="alfred-conv-empty-hint-icon alfred-chip alfred-chip--info" aria-hidden="true">1</span>
					<div>
						<strong>{{ __("Describe what you want") }}</strong>
						<p>{{ __("DocTypes, fields, notifications, scripts, workflows - plain English.") }}</p>
					</div>
				</div>
				<div class="alfred-card alfred-conv-empty-hint">
					<span class="alfred-conv-empty-hint-icon alfred-chip alfred-chip--info" aria-hidden="true">2</span>
					<div>
						<strong>{{ __("Review the changeset") }}</strong>
						<p>{{ __("Every proposed change shows up in the preview panel with a plain-English summary.") }}</p>
					</div>
				</div>
				<div class="alfred-card alfred-conv-empty-hint">
					<span class="alfred-conv-empty-hint-icon alfred-chip alfred-chip--info" aria-hidden="true">3</span>
					<div>
						<strong>{{ __("Approve and deploy") }}</strong>
						<p>{{ __("Nothing touches your site until you click Approve. Roll back anytime.") }}</p>
					</div>
				</div>
			</div>
			<div class="alfred-conv-empty-starters">
				<div class="alfred-eyebrow alfred-conv-empty-starters-label">{{ __("Try one of these") }}</div>
				<button
					v-for="ex in examples"
					:key="ex"
					type="button"
					class="alfred-card alfred-card--choice alfred-conv-empty-starter"
					@click="$emit('new-with-prompt', ex)"
				>
					<span class="alfred-conv-empty-starter-icon" aria-hidden="true">&rsaquo;</span>
					<span>{{ ex }}</span>
				</button>
			</div>
			<button
				type="button"
				class="alfred-btn-primary alfred-conv-empty-cta"
				@click="$emit('new-conversation')"
			>
				{{ __("Start a conversation") }}
			</button>
		</div>

		<!-- Conversation list with header -->
		<div v-else>
			<div class="alfred-conv-list-header">
				<span class="alfred-conv-list-title">{{ __("Conversations") }}</span>
				<button class="alfred-primary-btn alfred-primary-btn--gradient alfred-conv-list-new" @click="$emit('new-conversation')">
					<span class="alfred-btn-glyph" aria-hidden="true">+</span>
					<span>{{ __("New") }}</span>
				</button>
			</div>
			<div class="alfred-conv-items">
				<div
					v-for="conv in conversations"
					:key="conv.name"
					class="alfred-card alfred-conv-item"
					tabindex="0"
					role="button"
					@click="$emit('select', conv.name)"
					@keydown.enter="$emit('select', conv.name)"
				>
					<div class="alfred-conv-header">
						<div class="alfred-conv-header-left">
							<span :class="['alfred-chip', `alfred-chip--${modeChip(conv.mode)}`]">{{ modeLabel(conv.mode) }}</span>
							<span :class="['alfred-chip', `alfred-chip--${statusTone(conv.status)}`]">{{ conv.status }}</span>
							<span v-if="!conv.is_owner" class="alfred-chip alfred-chip--info alfred-conv-shared-badge">{{ __("Shared") }}</span>
						</div>
						<div class="alfred-conv-header-right">
							<button
								v-if="conv.is_owner"
								class="alfred-conv-action"
								:title="__('Share conversation')"
								@click.stop="$emit('share', conv.name)"
							>
								&#x1F517;
							</button>
							<button
								v-if="conv.is_owner"
								class="alfred-conv-action alfred-conv-delete"
								:title="__('Delete conversation')"
								@click.stop="$emit('delete', conv.name)"
							>
								&#x2715;
							</button>
						</div>
					</div>
					<div class="alfred-conv-preview">
						{{ conv.first_message || conv.name }}
					</div>
					<div class="alfred-conv-footer">
						<span class="alfred-conv-time">{{ frappe.datetime.prettyDate(conv.modified) }}</span>
						<span v-if="!conv.is_owner" class="alfred-conv-user text-muted">{{ conv.user }}</span>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
defineProps({
	conversations: { type: Array, default: () => [] },
});

defineEmits(["select", "new-conversation", "new-with-prompt", "delete", "share"]);

const examples = [
	"Create a DocType called Book with title, author, and ISBN fields",
	"Add an approval workflow to Leave Application with Draft, Pending, and Approved states",
	"Create a notification that emails the manager when a new expense claim is submitted",
];

// Map Alfred Conversation.status to a tone chip class. Keeps the old
// semantic meaning of each status but unifies the palette with the
// rest of the UI tokens.
const STATUS_TONES = {
	Open: "info",
	"In Progress": "info",
	"Awaiting Input": "warn",
	Completed: "success",
	Escalated: "danger",
	Failed: "danger",
	Cancelled: "neutral",
	Stale: "neutral",
};

function statusTone(status) {
	return STATUS_TONES[status] || "neutral";
}

// Map conversation.mode to a chip modifier class; fallback to auto.
function modeChip(mode) {
	const m = (mode || "auto").toLowerCase();
	return ["auto", "dev", "plan", "insights"].includes(m) ? m : "auto";
}

function modeLabel(mode) {
	const m = (mode || "auto").toLowerCase();
	if (m === "dev") return "Dev";
	if (m === "plan") return "Plan";
	if (m === "insights") return "Insights";
	return "Auto";
}
</script>

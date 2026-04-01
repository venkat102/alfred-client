<template>
	<div class="alfred-conversation-list">
		<!-- Onboarding: empty state -->
		<div v-if="!conversations.length" class="alfred-onboarding">
			<div class="text-center" style="padding: 30px 20px;">
				<h4 style="margin-bottom: 8px;">{{ __("Welcome to Alfred") }}</h4>
				<p class="text-muted" style="margin-bottom: 24px;">
					{{ __("I build Frappe customizations through conversation. Tell me what you need.") }}
				</p>
				<div class="alfred-example-prompts">
					<p class="text-muted text-xs" style="margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">
						{{ __("Try one of these") }}
					</p>
					<div
						v-for="ex in examples"
						:key="ex"
						class="alfred-example"
						@click="$emit('new-with-prompt', ex)"
					>
						{{ ex }}
					</div>
				</div>
				<button class="btn btn-primary btn-md" style="margin-top: 20px;" @click="$emit('new-conversation')">
					{{ __("Start a Conversation") }}
				</button>
			</div>
		</div>

		<!-- Conversation items -->
		<div v-else class="alfred-conv-items">
			<div
				v-for="conv in conversations"
				:key="conv.name"
				class="alfred-conv-item"
				tabindex="0"
				role="button"
				@click="$emit('select', conv.name)"
				@keydown.enter="$emit('select', conv.name)"
			>
				<div class="alfred-conv-header">
					<span :class="['indicator-pill', statusColor(conv.status)]">{{ conv.status }}</span>
					<span class="text-muted text-xs">{{ frappe.datetime.prettyDate(conv.modified) }}</span>
				</div>
				<div class="alfred-conv-summary">
					{{ conv.first_message || conv.name }}
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
defineProps({
	conversations: { type: Array, default: () => [] },
});

defineEmits(["select", "new-conversation", "new-with-prompt"]);

const examples = [
	"Create a DocType called Book with title, author, and ISBN fields",
	"Add an approval workflow to Leave Application with Draft, Pending, and Approved states",
	"Create a notification that emails the manager when a new expense claim is submitted",
];

const STATUS_COLORS = {
	Open: "blue", "In Progress": "orange", "Awaiting Input": "yellow",
	Completed: "green", Escalated: "red", Failed: "red", Stale: "gray",
};

function statusColor(status) {
	return STATUS_COLORS[status] || "gray";
}
</script>

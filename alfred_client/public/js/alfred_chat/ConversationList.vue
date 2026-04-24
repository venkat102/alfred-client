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
				data-testid="alfred-new-conversation"
				@click="$emit('new-conversation')"
			>
				{{ __("Start a conversation") }}
			</button>
		</div>

		<!-- Populated list: frosted topbar + time-grouped one-line rows.
		     Shares vocabulary with the chat shell (48px topbar, backdrop
		     blur, gradient + New). Search filters client-side; groups
		     are computed from conv.modified relative to now. -->
		<div v-else class="alfred-conv-view">
			<div class="alfred-conv-topbar">
				<div class="alfred-conv-topbar-zone alfred-conv-topbar-zone--left">
					<div class="alfred-mark alfred-mark--sm alfred-mark--chat" aria-hidden="true">A</div>
					<h2 class="alfred-conv-topbar-title">{{ __("Conversations") }}</h2>
				</div>
				<div class="alfred-conv-topbar-zone alfred-conv-topbar-zone--center">
					<input
						v-model.trim="searchQuery"
						type="search"
						class="alfred-conv-search"
						:placeholder="__('Search conversations...')"
						:aria-label="__('Search conversations')"
					/>
				</div>
				<div class="alfred-conv-topbar-zone alfred-conv-topbar-zone--right">
					<button
						class="alfred-primary-btn alfred-primary-btn--gradient alfred-conv-list-new"
						@click="$emit('new-conversation')"
					>
						<span class="alfred-btn-glyph" aria-hidden="true">+</span>
						<span>{{ __("New") }}</span>
					</button>
				</div>
			</div>
			<div class="alfred-conv-scroll">
				<div
					v-if="searchQuery && !filteredConversations.length"
					class="alfred-conv-noresults"
				>
					<span>{{ __("No conversations match") }} "{{ searchQuery }}".</span>
					<button class="alfred-conv-noresults-clear" @click="searchQuery = ''">
						{{ __("Clear search") }}
					</button>
				</div>
				<div
					v-for="group in groupedConversations"
					:key="group.label"
					class="alfred-conv-group"
				>
					<div class="alfred-eyebrow alfred-conv-group-label">{{ group.label }}</div>
					<div class="alfred-conv-rows">
						<div
							v-for="conv in group.rows"
							:key="conv.name"
							class="alfred-conv-row"
							tabindex="0"
							role="button"
							@click="$emit('select', conv.name)"
							@keydown.enter="$emit('select', conv.name)"
						>
							<span
								:class="['alfred-conv-row-dot', `alfred-conv-row-dot--${modeChip(conv.mode)}`]"
								:title="modeLabel(conv.mode)"
								aria-hidden="true"
							></span>
							<span
								v-if="conv.is_running"
								class="alfred-conv-row-live"
								:title="__('Pipeline running')"
								aria-hidden="true"
							></span>
							<div class="alfred-conv-row-title">
								{{ conv.first_message || conv.name }}
							</div>
							<div class="alfred-conv-row-meta">
								<span
									v-if="conv.latest_changeset_summary"
									class="alfred-conv-row-built"
									:title="conv.latest_changeset_summary"
								>{{ conv.latest_changeset_summary }}</span>
								<span
									v-if="conv.latest_changeset_state"
									:class="['alfred-chip', changesetChipClass(conv.latest_changeset_state), 'alfred-conv-row-state-chip']"
								>{{ changesetChipLabel(conv.latest_changeset_state) }}</span>
								<span
									v-else-if="statusVisible(conv.status)"
									:class="['alfred-chip', `alfred-chip--${statusTone(conv.status)}`, 'alfred-conv-row-status-chip']"
								>{{ conv.status }}</span>
								<span
									v-if="!conv.is_owner"
									class="alfred-chip alfred-chip--info alfred-conv-row-shared"
									:title="__('Shared by') + ' ' + conv.user"
								>{{ __("Shared") }}</span>
								<span class="alfred-conv-row-time">{{ formatTimeAndCount(conv.modified, conv.message_count) }}</span>
							</div>
							<div class="alfred-conv-row-actions">
								<button
									v-if="conv.is_owner"
									class="alfred-conv-row-action"
									:title="__('Share conversation')"
									@click.stop="$emit('share', conv.name)"
								>
									&#x1F517;
								</button>
								<button
									v-if="conv.is_owner"
									class="alfred-conv-row-action alfred-conv-row-action--delete"
									:title="__('Delete conversation')"
									@click.stop="$emit('delete', conv.name)"
								>
									&#x2715;
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { ref, computed } from "vue";

const props = defineProps({
	conversations: { type: Array, default: () => [] },
});

defineEmits(["select", "new-conversation", "new-with-prompt", "delete", "share"]);

const examples = [
	"Create a DocType called Book with title, author, and ISBN fields",
	"Add an approval workflow to Leave Application with Draft, Pending, and Approved states",
	"Create a notification that emails the manager when a new expense claim is submitted",
];

const searchQuery = ref("");

// Client-side search over first_message + name + user. Short enough that
// even a few hundred rows filter instantly; no need for server-side or
// debounce plumbing here.
const filteredConversations = computed(() => {
	const q = (searchQuery.value || "").toLowerCase().trim();
	if (!q) return props.conversations;
	return props.conversations.filter((c) => {
		const hay = (
			(c.first_message || "") + " " +
			(c.name || "") + " " +
			(c.user || "")
		).toLowerCase();
		return hay.includes(q);
	});
});

// Time buckets keyed to "now". Bucket thresholds measured in days from
// the start of today; "today" covers >= start-of-today, "yesterday" is
// the 24h window before that, then rolling 7/30/older windows.
const groupedConversations = computed(() => {
	const now = new Date();
	const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
	const dayMs = 86400000;
	const buckets = {
		today: { label: __("Today"), rows: [] },
		yesterday: { label: __("Yesterday"), rows: [] },
		last_7: { label: __("Last 7 days"), rows: [] },
		last_30: { label: __("Last 30 days"), rows: [] },
		older: { label: __("Older"), rows: [] },
	};
	for (const c of filteredConversations.value) {
		const ts = c.modified ? new Date(c.modified).getTime() : 0;
		if (ts >= startOfToday) buckets.today.rows.push(c);
		else if (ts >= startOfToday - dayMs) buckets.yesterday.rows.push(c);
		else if (ts >= startOfToday - 7 * dayMs) buckets.last_7.rows.push(c);
		else if (ts >= startOfToday - 30 * dayMs) buckets.last_30.rows.push(c);
		else buckets.older.rows.push(c);
	}
	return Object.values(buckets).filter((b) => b.rows.length > 0);
});

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

// Most statuses (Open / In Progress / Completed / Cancelled / Stale) are
// the normal lifecycle and would clutter every row if shown. Surface a
// chip only for the ones that need the user's attention, and only when
// there's no changeset state to show (the changeset chip wins since it
// carries more useful signal per row).
const VISIBLE_STATUSES = new Set(["Awaiting Input", "Failed", "Escalated"]);
function statusVisible(status) {
	return VISIBLE_STATUSES.has(status);
}

// Changeset state slug -> chip label + tone. The backend normalises
// Alfred Changeset.status to a snake_case slug before returning it.
const CHANGESET_CHIP = {
	pending:     { label: "Pending approval", tone: "warn" },
	approved:    { label: "Approved",         tone: "info" },
	deploying:   { label: "Deploying",        tone: "info" },
	deployed:    { label: "Deployed",         tone: "success" },
	rejected:    { label: "Rejected",         tone: "neutral" },
	rolled_back: { label: "Rolled back",      tone: "neutral" },
};

function changesetChipClass(state) {
	const tone = (CHANGESET_CHIP[state] || {}).tone || "neutral";
	return `alfred-chip--${tone}`;
}

function changesetChipLabel(state) {
	const entry = CHANGESET_CHIP[state];
	return entry ? __(entry.label) : state;
}

// "2h ago · 12 msgs". Singular "msg" at 1, suffix omitted at 0 so
// brand-new conversations don't read as "0 msgs".
function formatTimeAndCount(modified, count) {
	const t = frappe.datetime.prettyDate(modified);
	if (!count) return t;
	if (count === 1) return `${t} · 1 msg`;
	return `${t} · ${count} msgs`;
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

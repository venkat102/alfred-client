<template>
	<div class="alfred-card alfred-plan-doc">
		<div class="alfred-plan-header">
			<div class="alfred-plan-header-left">
				<div class="alfred-mark alfred-mark--preview alfred-mark--sm" aria-hidden="true">&#9670;</div>
				<div class="alfred-plan-title">{{ plan.title || __("Plan") }}</div>
			</div>
			<span v-if="statusLabel" :class="['alfred-chip', `alfred-chip--${statusChipTone}`]">
				{{ statusLabel }}
			</span>
		</div>

		<p v-if="plan.summary" class="alfred-plan-summary">{{ plan.summary }}</p>

		<div v-if="plan.steps && plan.steps.length" class="alfred-plan-section">
			<div class="alfred-eyebrow alfred-plan-section-title">{{ __("Steps") }}</div>
			<ol class="alfred-plan-steps">
				<li v-for="(step, i) in plan.steps" :key="step.order || i" class="alfred-card alfred-plan-step">
					<span class="alfred-plan-step-ordinal" aria-hidden="true">{{ i + 1 }}</span>
					<div class="alfred-plan-step-body">
						<div class="alfred-plan-step-action">{{ step.action }}</div>
						<div v-if="step.rationale" class="alfred-plan-step-rationale">
							{{ step.rationale }}
						</div>
						<div v-if="step.doctype" class="alfred-plan-step-doctype">
							<span class="alfred-chip alfred-chip--neutral"><code>{{ step.doctype }}</code></span>
						</div>
					</div>
				</li>
			</ol>
		</div>

		<div v-if="plan.doctypes_touched && plan.doctypes_touched.length" class="alfred-plan-section">
			<div class="alfred-eyebrow alfred-plan-section-title">{{ __("Doctypes touched") }}</div>
			<div class="alfred-plan-doctypes">
				<span v-for="dt in plan.doctypes_touched" :key="dt" class="alfred-chip alfred-chip--neutral">
					<code>{{ dt }}</code>
				</span>
			</div>
		</div>

		<div v-if="plan.risks && plan.risks.length" class="alfred-card alfred-card--warn alfred-plan-section alfred-plan-callout">
			<div class="alfred-eyebrow alfred-plan-callout-title">{{ __("Risks") }}</div>
			<ul class="alfred-plan-callout-list">
				<li v-for="(risk, i) in plan.risks" :key="i">{{ risk }}</li>
			</ul>
		</div>

		<div v-if="plan.open_questions && plan.open_questions.length" class="alfred-card alfred-card--info alfred-plan-section alfred-plan-callout">
			<div class="alfred-eyebrow alfred-plan-callout-title">{{ __("Open questions") }}</div>
			<ul class="alfred-plan-callout-list">
				<li v-for="(q, i) in plan.open_questions" :key="i">{{ q }}</li>
			</ul>
		</div>

		<div v-if="plan.estimated_items" class="alfred-plan-estimated">
			{{ __("Estimated changeset size: {0} item(s)", [plan.estimated_items]) }}
		</div>

		<div v-if="showActions" class="alfred-plan-actions">
			<button class="alfred-btn-ghost" @click="$emit('refine')">
				{{ __("Refine") }}
			</button>
			<button class="alfred-btn-primary alfred-btn-primary--success" @click="$emit('approve')">
				{{ __("Approve and Build") }}
			</button>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
	plan: { type: Object, required: true },
});

defineEmits(["refine", "approve"]);

const statusLabel = computed(() => {
	const s = (props.plan.status || "").toLowerCase();
	if (s === "approved") return "Approved";
	if (s === "built") return "Built";
	if (s === "rejected") return "Rejected";
	if (s === "proposed") return "Proposed";
	return "";
});

// Map plan status to chip tone. Four distinct tones so "Built" and
// "Proposed" no longer read as the same shade of indigo:
//   proposed -> info  (blue, waiting on a decision)
//   approved -> success (green, greenlit, build about to start)
//   built    -> finished (dark slate via .alfred-chip--finished)
//   rejected -> danger  (red, closed)
const statusChipTone = computed(() => {
	const s = (props.plan.status || "proposed").toLowerCase();
	if (s === "approved") return "success";
	if (s === "built") return "finished";
	if (s === "rejected") return "danger";
	return "info";
});

// Hide the Refine / Approve buttons once the plan has been approved,
// built, or rejected - at that point the decision is already made and
// showing buttons would be confusing.
const showActions = computed(() => {
	const s = (props.plan.status || "proposed").toLowerCase();
	return s === "proposed";
});
</script>

<style scoped>
.alfred-plan-doc {
	padding: 16px;
	margin: 4px 0;
}

.alfred-plan-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 12px;
	margin-bottom: 10px;
}

.alfred-plan-header-left {
	display: flex;
	align-items: center;
	gap: 10px;
	min-width: 0;
}

.alfred-plan-title {
	font-weight: 600;
	font-size: 15px;
	color: var(--text-color, #111827);
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.alfred-plan-summary {
	font-size: 13px;
	line-height: 1.5;
	margin: 0 0 12px 0;
	color: var(--text-color, #374151);
}

.alfred-plan-section {
	margin-top: 12px;
}

.alfred-plan-section-title {
	margin-bottom: 6px;
}

.alfred-plan-steps {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.alfred-plan-step {
	display: flex;
	gap: 12px;
	padding: 10px 12px;
	font-size: 13px;
}

.alfred-plan-step-ordinal {
	flex-shrink: 0;
	width: 24px;
	height: 24px;
	border-radius: 999px;
	background: var(--alfred-mode-plan-bg, #eef2ff);
	color: var(--alfred-mode-plan-fg, #4338ca);
	font-size: 11px;
	font-weight: 700;
	display: inline-flex;
	align-items: center;
	justify-content: center;
	line-height: 1;
}

.alfred-plan-step-body {
	flex: 1;
	min-width: 0;
}

.alfred-plan-step-action {
	font-weight: 500;
	color: var(--text-color, #111827);
	margin-bottom: 3px;
}

.alfred-plan-step-rationale {
	margin-top: 2px;
	color: var(--text-muted, #6b7280);
	font-size: 12px;
	line-height: 1.5;
}

.alfred-plan-step-doctype {
	margin-top: 6px;
}

.alfred-plan-step-doctype code,
.alfred-plan-doctypes code {
	font-size: 11px;
	background: none;
	padding: 0;
}

.alfred-plan-doctypes {
	display: flex;
	flex-wrap: wrap;
	gap: 4px;
}

/* Callout cards (risks = warn, open questions = info) - compose the
 * Phase 1 tone cards with an internal list layout. */
.alfred-plan-callout {
	padding: 10px 14px;
}

.alfred-plan-callout-title {
	margin-bottom: 6px;
}

.alfred-plan-callout-list {
	margin: 0;
	padding-left: 18px;
	font-size: 13px;
	line-height: 1.5;
}

.alfred-plan-callout-list li {
	margin-bottom: 4px;
}

.alfred-plan-estimated {
	margin-top: 12px;
	font-size: 12px;
	color: var(--text-muted, #6b7280);
}

.alfred-plan-actions {
	margin-top: 16px;
	display: flex;
	gap: 8px;
	justify-content: flex-end;
}
</style>

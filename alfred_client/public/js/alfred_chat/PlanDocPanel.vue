<template>
	<div class="alfred-plan-doc">
		<div class="alfred-plan-header">
			<div class="alfred-plan-title">{{ plan.title || __("Plan") }}</div>
			<span v-if="statusLabel" :class="['alfred-plan-status', `alfred-plan-status-${plan.status || 'proposed'}`]">
				{{ statusLabel }}
			</span>
		</div>

		<p v-if="plan.summary" class="alfred-plan-summary">{{ plan.summary }}</p>

		<div v-if="plan.steps && plan.steps.length" class="alfred-plan-section">
			<div class="alfred-plan-section-title">{{ __("Steps") }}</div>
			<ol class="alfred-plan-steps">
				<li v-for="step in plan.steps" :key="step.order" class="alfred-plan-step">
					<div class="alfred-plan-step-action">{{ step.action }}</div>
					<div v-if="step.rationale" class="alfred-plan-step-rationale text-muted text-xs">
						{{ step.rationale }}
					</div>
					<div v-if="step.doctype" class="alfred-plan-step-doctype">
						<code>{{ step.doctype }}</code>
					</div>
				</li>
			</ol>
		</div>

		<div v-if="plan.doctypes_touched && plan.doctypes_touched.length" class="alfred-plan-section">
			<div class="alfred-plan-section-title">{{ __("Doctypes touched") }}</div>
			<div class="alfred-plan-doctypes">
				<code v-for="dt in plan.doctypes_touched" :key="dt">{{ dt }}</code>
			</div>
		</div>

		<div v-if="plan.risks && plan.risks.length" class="alfred-plan-section">
			<div class="alfred-plan-section-title">{{ __("Risks") }}</div>
			<ul class="alfred-plan-risks">
				<li v-for="(risk, i) in plan.risks" :key="i">{{ risk }}</li>
			</ul>
		</div>

		<div v-if="plan.open_questions && plan.open_questions.length" class="alfred-plan-section">
			<div class="alfred-plan-section-title">{{ __("Open questions") }}</div>
			<ul class="alfred-plan-questions">
				<li v-for="(q, i) in plan.open_questions" :key="i">{{ q }}</li>
			</ul>
		</div>

		<div v-if="plan.estimated_items" class="alfred-plan-estimated text-muted text-xs">
			{{ __("Estimated changeset size: {0} item(s)", [plan.estimated_items]) }}
		</div>

		<div v-if="showActions" class="alfred-plan-actions">
			<button class="btn btn-default btn-sm alfred-plan-btn-refine" @click="$emit('refine')">
				{{ __("Refine") }}
			</button>
			<button class="btn btn-primary btn-sm alfred-plan-btn-approve" @click="$emit('approve')">
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
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 8px;
	padding: 16px;
	background: var(--bg-secondary, #f9fafb);
	margin: 4px 0;
}

.alfred-plan-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
	margin-bottom: 8px;
}

.alfred-plan-title {
	font-weight: 600;
	font-size: 15px;
	color: var(--text-color, #111827);
}

.alfred-plan-status {
	font-size: 11px;
	font-weight: 500;
	padding: 2px 8px;
	border-radius: 12px;
	text-transform: uppercase;
	letter-spacing: 0.5px;
}

.alfred-plan-status-proposed {
	background: #eef2ff;
	color: #4338ca;
}

.alfred-plan-status-approved {
	background: #dcfce7;
	color: #166534;
}

.alfred-plan-status-built {
	background: #e0e7ff;
	color: #3730a3;
}

.alfred-plan-status-rejected {
	background: #fee2e2;
	color: #991b1b;
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
	font-size: 11px;
	font-weight: 600;
	text-transform: uppercase;
	letter-spacing: 0.5px;
	color: var(--text-muted, #6b7280);
	margin-bottom: 4px;
}

.alfred-plan-steps {
	margin: 0;
	padding-left: 20px;
}

.alfred-plan-step {
	margin-bottom: 8px;
	font-size: 13px;
}

.alfred-plan-step-action {
	font-weight: 500;
	color: var(--text-color, #111827);
}

.alfred-plan-step-rationale {
	margin-top: 2px;
}

.alfred-plan-step-doctype {
	margin-top: 2px;
}

.alfred-plan-step-doctype code,
.alfred-plan-doctypes code {
	font-size: 11px;
	padding: 1px 6px;
	background: var(--bg-tertiary, #e5e7eb);
	border-radius: 3px;
	margin-right: 4px;
}

.alfred-plan-doctypes {
	display: flex;
	flex-wrap: wrap;
	gap: 4px;
}

.alfred-plan-risks,
.alfred-plan-questions {
	margin: 0;
	padding-left: 20px;
	font-size: 13px;
	line-height: 1.5;
}

.alfred-plan-risks li,
.alfred-plan-questions li {
	margin-bottom: 4px;
}

.alfred-plan-estimated {
	margin-top: 12px;
}

.alfred-plan-actions {
	margin-top: 16px;
	display: flex;
	gap: 8px;
	justify-content: flex-end;
}
</style>

<template>
	<div
		class="alfred-status-pill-wrap"
		ref="wrapEl"
	>
		<button
			type="button"
			:class="[
				'alfred-status-pill',
				`alfred-status-pill--${state}`,
				{ 'alfred-status-pill--clickable': isExpandable },
			]"
			role="status"
			aria-live="polite"
			:aria-expanded="isExpandable ? open : undefined"
			:aria-haspopup="isExpandable ? 'dialog' : undefined"
			:tabindex="isExpandable ? 0 : -1"
			@click="onClick"
		>
			<!-- Idle / outcome states: simple dot + label -->
			<template v-if="state === 'idle' || state === 'outcome-success' || state === 'outcome-error'">
				<span class="alfred-status-pill-dot" aria-hidden="true">
					<span class="alfred-status-pill-dot-halo"></span>
				</span>
				<span class="alfred-status-pill-label">{{ displayLabel }}</span>
				<span v-if="elapsed" class="alfred-status-pill-elapsed">{{ elapsed }}s</span>
			</template>

			<!-- Processing: pulsing gradient mark + agent + ticker -->
			<template v-else>
				<span class="alfred-status-pill-mark" aria-hidden="true">
					<span class="alfred-status-pill-mark-ring"></span>
					<span class="alfred-status-pill-mark-core">A</span>
				</span>
				<span class="alfred-status-pill-body">
					<span class="alfred-status-pill-agent">{{ agentName || __('Working') }}</span>
					<span v-if="activity" class="alfred-status-pill-activity">{{ activity }}</span>
				</span>
				<span v-if="elapsed" class="alfred-status-pill-elapsed">{{ elapsed }}s</span>
				<span v-if="isExpandable" class="alfred-status-pill-caret" aria-hidden="true">
					{{ open ? '\u25B4' : '\u25BE' }}
				</span>
			</template>
		</button>

		<!-- Popover: six-step pipeline + recent ticker history -->
		<div
			v-if="open && isExpandable"
			class="alfred-status-pill-popover"
			role="dialog"
			:aria-label="__('Pipeline details')"
		>
			<div class="alfred-status-pill-popover-head">
				<span class="alfred-eyebrow">{{ __('Pipeline') }}</span>
				<button
					type="button"
					class="alfred-status-pill-popover-close"
					@click="$emit('update:open', false)"
					:aria-label="__('Close pipeline details')"
				>
					&#10005;
				</button>
			</div>
			<div class="alfred-status-pill-popover-body">
				<PhasePipeline
					v-if="pipelineMode !== 'lite'"
					:current-phase="currentPhase"
					:completed-phases="completedPhases"
					:active-agent="agentName"
				/>
				<div v-else class="alfred-status-pill-popover-lite">
					<span class="alfred-chip alfred-chip--plan">{{ __('Basic mode') }}</span>
					<p>{{ __('Single-agent fast pipeline.') }}</p>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import PhasePipeline from "./PhasePipeline.vue";

const props = defineProps({
	state: { type: String, default: "idle" }, // idle | processing | outcome-success | outcome-error
	agentName: { type: String, default: "" },
	activity: { type: String, default: "" },
	elapsed: { type: [String, Number], default: "" },
	pipelineMode: { type: String, default: "full" },
	currentPhase: { type: String, default: null },
	completedPhases: { type: Array, default: () => [] },
	label: { type: String, default: "" },
	open: { type: Boolean, default: false },
});

const emit = defineEmits(["update:open", "click"]);

const wrapEl = ref(null);
defineExpose({ wrapEl });

const isExpandable = computed(() => props.state === "processing");

const displayLabel = computed(() => {
	if (props.label) return props.label;
	if (props.state === "outcome-success") return __("Completed");
	if (props.state === "outcome-error") return __("Failed");
	return __("Ready");
});

function onClick() {
	emit("click");
	if (isExpandable.value) {
		emit("update:open", !props.open);
	}
}
</script>

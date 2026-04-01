<template>
	<div class="alfred-phase-pipeline">
		<template v-for="(phase, idx) in phases" :key="phase.key">
			<span v-if="idx > 0" class="alfred-phase-arrow">&rsaquo;</span>
			<span :class="['alfred-phase', phaseClass(phase.key)]" :data-phase="phase.key">
				<span class="alfred-phase-step">{{ isCompleted(phase.key) ? '✓' : idx + 1 }}</span>
				<span class="alfred-phase-label">{{ phase.label }}</span>
			</span>
		</template>
	</div>
</template>

<script setup>
const props = defineProps({
	currentPhase: { type: String, default: null },
	completedPhases: { type: Array, default: () => [] },
});

const phases = [
	{ key: "requirement", label: "Requirements" },
	{ key: "assessment", label: "Assessment" },
	{ key: "architecture", label: "Architecture" },
	{ key: "development", label: "Development" },
	{ key: "testing", label: "Testing" },
	{ key: "deployment", label: "Deployment" },
];

function isCompleted(key) {
	return props.completedPhases.includes(key);
}

function phaseClass(key) {
	if (props.currentPhase === key) return "alfred-phase-active";
	if (isCompleted(key)) return "alfred-phase-done";
	return "";
}
</script>

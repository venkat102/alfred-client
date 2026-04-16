<template>
	<div class="alfred-phase-pipeline">
		<template v-for="(phase, idx) in phases" :key="phase.key">
			<span v-if="idx > 0" class="alfred-phase-arrow">&rsaquo;</span>
			<span :class="['alfred-phase', phaseClass(phase.key)]" :data-phase="phase.key">
				<span class="alfred-phase-step">
					<span v-if="currentPhase === phase.key" class="alfred-phase-pulse" aria-hidden="true"></span>
					<template v-else-if="isCompleted(phase.key)">&#10003;</template>
					<template v-else>{{ idx + 1 }}</template>
				</span>
				<span class="alfred-phase-label">
					<template v-if="currentPhase === phase.key">
						<span class="alfred-phase-agent">{{ activeAgent || phase.label }}</span>
						<span class="alfred-phase-activity">&middot; {{ STEP_LABELS[phase.key] }}</span>
					</template>
					<template v-else>
						{{ phase.label }}
					</template>
				</span>
			</span>
		</template>
	</div>
</template>

<script setup>
const props = defineProps({
	currentPhase: { type: String, default: null },
	completedPhases: { type: Array, default: () => [] },
	// Live agent name (e.g. "Developer", "Requirement Analyst") for the
	// active phase. Shown in place of the static phase label so the user
	// can see WHO is working, not just WHICH stage. Falls back to the
	// phase label if no agent is attached yet (briefly, between the
	// phase transition and the first agent_status event).
	activeAgent: { type: String, default: null },
});

const phases = [
	{ key: "requirement", label: "Requirements" },
	{ key: "assessment", label: "Assessment" },
	{ key: "architecture", label: "Architecture" },
	{ key: "development", label: "Development" },
	{ key: "testing", label: "Testing" },
	{ key: "deployment", label: "Deployment" },
];

// Short activity phrases per phase - these are written in present-
// participle form so they read as "what is happening right now"
// (e.g. "Developer - generating code"). The pipeline uses them as
// the trailing fragment on the active pill.
const STEP_LABELS = {
	requirement: "gathering requirements",
	assessment: "checking feasibility",
	architecture: "designing solution",
	development: "generating code",
	testing: "validating changeset",
	deployment: "preparing deployment",
};

function isCompleted(key) {
	return props.completedPhases.includes(key);
}

function phaseClass(key) {
	if (props.currentPhase === key) return "alfred-phase-active";
	if (isCompleted(key)) return "alfred-phase-done";
	return "";
}
</script>

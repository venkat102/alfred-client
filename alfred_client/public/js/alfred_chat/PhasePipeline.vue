<template>
	<div class="alfred-pipeline" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
		<!-- Progress rail: a single line the step markers sit on. The
		     filled portion grows as phases complete so the eye has a
		     smooth percentage anchor even when six chips are compressed. -->
		<div class="alfred-pipeline-rail" aria-hidden="true">
			<div class="alfred-pipeline-rail-fill" :style="{ width: `${progress}%` }"></div>
		</div>
		<div class="alfred-pipeline-steps">
			<div
				v-for="(phase, idx) in phases"
				:key="phase.key"
				:class="[
					'alfred-pipeline-step',
					isDone(phase.key) && 'alfred-pipeline-step--done',
					currentPhase === phase.key && 'alfred-pipeline-step--current',
				]"
				:data-phase="phase.key"
			>
				<span class="alfred-pipeline-marker" aria-hidden="true">
					<template v-if="currentPhase === phase.key">
						<span class="alfred-pipeline-marker-ring"></span>
						<span class="alfred-pipeline-marker-core"></span>
					</template>
					<template v-else-if="isDone(phase.key)">
						&#10003;
					</template>
					<template v-else>
						{{ idx + 1 }}
					</template>
				</span>
				<span class="alfred-pipeline-label">
					<template v-if="currentPhase === phase.key">
						<span class="alfred-pipeline-agent">{{ activeAgent || phase.label }}</span>
						<span class="alfred-pipeline-activity">{{ STEP_LABELS[phase.key] }}</span>
					</template>
					<template v-else>{{ phase.label }}</template>
				</span>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

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

// Short activity phrases per phase - present-participle so they read as
// "what is happening right now" next to the live agent name.
const STEP_LABELS = {
	requirement: "gathering requirements",
	assessment: "checking feasibility",
	architecture: "designing solution",
	development: "generating code",
	testing: "validating changeset",
	deployment: "preparing deployment",
};

function isDone(key) {
	return props.completedPhases.includes(key);
}

// Overall progress percentage for the rail fill. Counts completed
// phases plus a half-step credit for the current one so the bar never
// sits exactly on a step boundary (it always looks like we're moving).
const progress = computed(() => {
	const total = phases.length;
	const done = props.completedPhases.length;
	const mid = props.currentPhase ? 0.5 : 0;
	return Math.min(100, Math.round(((done + mid) / total) * 100));
});
</script>

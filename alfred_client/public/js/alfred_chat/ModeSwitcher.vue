<template>
	<div class="alfred-mode-switcher" role="group" :aria-label="__('Chat mode')">
		<button
			v-for="opt in options"
			:key="opt.value"
			:class="[
				'alfred-mode-btn',
				`alfred-mode-btn-${opt.value}`,
				{ 'alfred-mode-btn-active': modelValue === opt.value },
			]"
			:title="opt.tooltip"
			@click="$emit('update:modelValue', opt.value)"
		>
			{{ opt.label }}
		</button>
	</div>
</template>

<script setup>
defineProps({
	modelValue: {
		type: String,
		default: "auto",
		validator: (v) => ["auto", "dev", "plan", "insights"].includes(v),
	},
});

defineEmits(["update:modelValue"]);

const options = [
	{
		value: "auto",
		label: "Auto",
		tooltip: "Alfred decides the mode based on your prompt",
	},
	{
		value: "dev",
		label: "Dev",
		tooltip: "Force build/deploy mode - runs the full 6-agent SDLC crew",
	},
	{
		value: "plan",
		label: "Plan",
		tooltip: "Force planning mode - describes the approach without building",
	},
	{
		value: "insights",
		label: "Insights",
		tooltip: "Force read-only mode - answers questions about your site state",
	},
];
</script>

<style scoped>
.alfred-mode-switcher {
	display: inline-flex;
	gap: 2px;
	padding: 2px;
	background: var(--bg-tertiary, #f3f4f6);
	border-radius: 6px;
	border: 1px solid var(--border-color, #e5e7eb);
}

.alfred-mode-btn {
	padding: 4px 10px;
	font-size: 12px;
	font-weight: 500;
	color: var(--text-muted, #6b7280);
	background: transparent;
	border: none;
	border-radius: 4px;
	cursor: pointer;
	transition: background 0.12s ease, color 0.12s ease;
}

.alfred-mode-btn:hover {
	color: var(--text-color, #111827);
	background: var(--bg-secondary, #ffffff);
}

.alfred-mode-btn-active {
	background: var(--bg-color, #ffffff);
	color: var(--text-color, #111827);
	box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.alfred-mode-btn-active.alfred-mode-btn-dev {
	color: #1d4ed8;
}

.alfred-mode-btn-active.alfred-mode-btn-plan {
	color: #4338ca;
}

.alfred-mode-btn-active.alfred-mode-btn-insights {
	color: #0891b2;
}

.alfred-mode-btn-active.alfred-mode-btn-auto {
	color: #047857;
}
</style>

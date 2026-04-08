<script setup lang="ts">
import { computed } from 'vue'

import type { CodexGoalLoopAction, CodexGoalLoopState } from '../types/api'

const props = defineProps<{
  state: CodexGoalLoopState | null
  goal: string
  definitionOfDone: string
  saving: boolean
}>()

const emit = defineEmits<{
  'update:goal': [value: string]
  'update:definitionOfDone': [value: string]
  save: []
  action: [action: CodexGoalLoopAction]
}>()

const statusLabel = computed(() => {
  const status = props.state?.status ?? 'idle'
  const labels: Record<string, string> = {
    idle: 'Idle',
    running: 'Running',
    paused: 'Paused',
    blocked: 'Blocked',
    completed: 'Completed',
    failed: 'Failed',
  }
  return labels[status] ?? 'Idle'
})

const statusClass = computed(() => {
  const status = props.state?.status ?? 'idle'
  if (status === 'running') {
    return 'border-[rgba(21,94,99,0.16)] bg-[rgba(214,238,234,0.95)] text-[color:var(--app-accent-deep)]'
  }
  if (status === 'completed') {
    return 'border-[rgba(75,139,88,0.16)] bg-[rgba(227,241,229,0.96)] text-[#3f6d47]'
  }
  if (status === 'blocked' || status === 'failed') {
    return 'border-[rgba(184,93,72,0.16)] bg-[rgba(249,233,228,0.96)] text-[#9a4c3d]'
  }
  if (status === 'paused') {
    return 'border-[rgba(143,112,52,0.16)] bg-[rgba(248,239,220,0.96)] text-[#7d6332]'
  }
  return 'border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.96)] text-[color:var(--app-text-soft)]'
})

const goalPreview = computed(() => props.state?.goal?.trim() || props.goal.trim() || 'No goal yet')
const lastReason = computed(() => props.state?.last_reason?.trim() || '')
const iterationLabel = computed(() => {
  const iteration = props.state?.iteration_count ?? 0
  const total = props.state?.max_iterations ?? 8
  return `${iteration}/${total}`
})

const canStart = computed(() => {
  const status = props.state?.status ?? 'idle'
  return (
    ['idle', 'paused', 'blocked', 'failed', 'completed'].includes(status) &&
    props.goal.trim().length > 0 &&
    props.definitionOfDone.trim().length > 0
  )
})
const canPause = computed(() => props.state?.status === 'running')
const canResume = computed(() => ['paused', 'blocked', 'failed'].includes(props.state?.status ?? ''))
const startButtonLabel = computed(() => (canResume.value ? 'Resume' : 'Start'))
const startButtonIcon = computed(() => (canResume.value ? 'pi pi-refresh' : 'pi pi-play'))
const primaryAction = computed(() => {
  if (canPause.value) {
    return {
      label: 'Pause',
      icon: 'pi pi-pause',
      action: 'pause' as CodexGoalLoopAction,
      disabled: props.saving || !canPause.value,
    }
  }
  return {
    label: canResume.value ? 'Resume' : 'Start',
    icon: startButtonIcon.value,
    action: (canResume.value ? 'resume' : 'start') as CodexGoalLoopAction,
    disabled: props.saving || !canStart.value,
  }
})

const utilityActions = computed(() => [
  {
    label: 'Complete',
    icon: 'pi pi-check',
    action: 'complete' as CodexGoalLoopAction,
    disabled: props.saving,
    tone: 'neutral',
  },
  {
    label: 'Clear',
    icon: 'pi pi-times',
    action: 'clear' as CodexGoalLoopAction,
    disabled: props.saving,
    tone: 'danger',
  },
])
</script>

<template>
  <section
    class="grid gap-4 rounded-[1.4rem] border border-[rgba(34,66,72,0.08)] bg-[linear-gradient(145deg,rgba(255,252,246,0.98),rgba(245,239,229,0.92))] p-4 shadow-[0_14px_28px_rgba(31,54,58,0.08)] max-sm:gap-3 max-sm:rounded-[1.2rem] max-sm:p-3.5"
  >
    <div class="flex items-start justify-between gap-3 max-sm:flex-col max-sm:items-stretch">
      <div class="min-w-0">
        <p class="eyebrow">Ralph Loop</p>
        <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[1.08rem] font-semibold leading-[1.1]">
          Goal-driven background execution
        </h3>
        <p class="mt-1 mb-0 text-[0.88rem] leading-[1.55] text-[color:var(--app-text-soft)]">
          Keep the same Codex thread working until it finishes, blocks, or you pause it.
        </p>
      </div>
      <div class="flex items-center gap-2 self-start max-sm:self-auto">
        <span
          class="inline-flex items-center rounded-full border px-2.5 py-1 text-[0.74rem] font-bold uppercase tracking-[0.08em]"
          :class="statusClass"
        >
          {{ statusLabel }}
        </span>
        <span class="rounded-full bg-white/70 px-2.5 py-1 text-[0.75rem] font-semibold text-[color:var(--app-text-soft)]">
          {{ iterationLabel }}
        </span>
        <button
          type="button"
          class="goal-loop-save-button"
          :disabled="saving"
          aria-label="Save"
          @click="emit('save')"
        >
          <i class="pi pi-save text-[0.78rem]"></i>
          <span>Save</span>
        </button>
      </div>
    </div>

    <div class="grid gap-2 rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-white/70 px-3 py-2.5">
      <div>
        <p class="m-0 text-[0.74rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
          Goal preview
        </p>
        <p class="mt-1 mb-0 line-clamp-2 text-[0.95rem] leading-[1.55]">
          {{ goalPreview }}
        </p>
      </div>
      <p
        v-if="lastReason"
        class="m-0 rounded-[0.9rem] bg-[rgba(255,252,247,0.92)] px-2.5 py-2 text-[0.84rem] leading-[1.5] text-[color:var(--app-text-soft)]"
      >
        {{ lastReason }}
      </p>
    </div>

    <label class="grid gap-1.5">
      <span class="text-[0.78rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
        Goal
      </span>
      <textarea
        :value="goal"
        class="min-h-24 rounded-[1rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-3 py-2.5 text-[0.95rem] leading-[1.55] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
        placeholder="Ship the feature, close the bug, or complete the repo task."
        @input="emit('update:goal', ($event.target as HTMLTextAreaElement).value)"
      />
    </label>

    <label class="grid gap-1.5">
      <span class="text-[0.78rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
        Definition of done
      </span>
      <textarea
        :value="definitionOfDone"
        class="min-h-24 rounded-[1rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-3 py-2.5 text-[0.95rem] leading-[1.55] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
        placeholder="Describe the acceptance condition Codex must satisfy before it can stop."
        @input="emit('update:definitionOfDone', ($event.target as HTMLTextAreaElement).value)"
      />
    </label>

    <div class="goal-loop-control-deck">
      <button
        type="button"
        class="goal-loop-primary-action"
        :disabled="primaryAction.disabled"
        :aria-label="primaryAction.label"
        @click="emit('action', primaryAction.action)"
      >
        <span class="goal-loop-primary-action__icon">
          <i class="pi" :class="primaryAction.icon"></i>
        </span>
        <span class="goal-loop-primary-action__text">
          {{ primaryAction.label }}
        </span>
      </button>

      <div class="goal-loop-utility-rail" aria-label="Loop utility actions">
        <button
          v-for="item in utilityActions"
          :key="item.action"
          type="button"
          class="goal-loop-utility-action"
          :class="item.tone === 'danger' ? 'goal-loop-utility-action--danger' : ''"
          :disabled="item.disabled"
          :aria-label="item.label"
          @click="emit('action', item.action)"
        >
          <i class="pi" :class="item.icon"></i>
          <span>{{ item.label }}</span>
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import Button from 'primevue/button'

import CodexGoalLoopCard from './CodexGoalLoopCard.vue'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const props = defineProps<{
  mobile?: boolean
  closeable?: boolean
}>()

const workspace = useWorkspaceAppContext()

const modeMeta = [
  {
    id: 'ralph-loop',
    label: 'Ralph Loop',
    description: 'Set one goal and let Codex keep iterating on the same thread.',
  },
] as const

const selectedModeLabel = computed(() => {
  return (
    modeMeta.find((item) => item.id === workspace.selectedCodexAdvancedMode)?.label ??
    'Ralph Loop'
  )
})
</script>

<template>
  <section
    class="advanced-mode-panel grid gap-4 rounded-[1.55rem] border border-[rgba(34,66,72,0.08)] bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(248,243,235,0.96))] p-4 shadow-[0_22px_56px_rgba(31,54,58,0.12)] backdrop-blur-[18px] max-sm:gap-3 max-sm:rounded-[1.35rem] max-sm:p-3.5"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="eyebrow">Advanced Mode</p>
        <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[1.18rem] font-semibold leading-[1.08]">
          Background behaviors for this Codex session
        </h3>
        <p class="mt-1 mb-0 text-[0.9rem] leading-[1.55] text-[color:var(--app-text-soft)]">
          Keep the chat composer clean, then open the mode you need only when you need it.
        </p>
      </div>
      <Button
        v-if="props.closeable"
        icon="pi pi-times"
        rounded
        text
        severity="secondary"
        aria-label="Close advanced mode"
        @click="workspace.closeCodexAdvancedMode"
      />
    </div>

    <div class="grid gap-2">
      <div class="flex flex-wrap gap-2">
        <button
          v-for="mode in modeMeta"
          :key="mode.id"
          type="button"
          class="rounded-full border px-3 py-2 text-left transition"
          :class="
            workspace.selectedCodexAdvancedMode === mode.id
              ? 'border-[rgba(21,94,99,0.18)] bg-[rgba(214,238,234,0.9)] text-[color:var(--app-accent-deep)] shadow-[0_10px_24px_rgba(21,94,99,0.08)]'
              : 'border-[rgba(34,66,72,0.1)] bg-white/70 text-[color:var(--app-text-soft)] hover:border-[rgba(21,94,99,0.18)] hover:text-[color:var(--app-text)]'
          "
          :aria-pressed="workspace.selectedCodexAdvancedMode === mode.id"
          @click="workspace.selectCodexAdvancedMode(mode.id)"
        >
          <span class="block text-[0.84rem] font-semibold">{{ mode.label }}</span>
          <span class="mt-0.5 block text-[0.77rem] leading-[1.4] opacity-85">
            {{ mode.description }}
          </span>
        </button>
      </div>
      <div class="rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-white/60 px-3 py-2.5">
        <div class="flex items-center justify-between gap-3 max-sm:flex-col max-sm:items-start">
          <div class="min-w-0">
            <p class="m-0 text-[0.74rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
              Selected mode
            </p>
            <p class="mt-1 mb-0 text-[0.95rem] font-semibold">{{ selectedModeLabel }}</p>
          </div>
          <div class="flex items-center gap-2">
            <span
              class="inline-flex items-center rounded-full border px-2.5 py-1 text-[0.74rem] font-bold uppercase tracking-[0.08em]"
              :class="
                workspace.isCodexGoalLoopRunning
                  ? 'border-[rgba(21,94,99,0.16)] bg-[rgba(214,238,234,0.95)] text-[color:var(--app-accent-deep)]'
                  : 'border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.96)] text-[color:var(--app-text-soft)]'
              "
            >
              {{ workspace.codexGoalLoopShortStatus }}
            </span>
            <span class="rounded-full bg-white/70 px-2.5 py-1 text-[0.76rem] font-semibold text-[color:var(--app-text-soft)]">
              {{ workspace.activeCodexGoalLoop?.iteration_count ?? 0 }}/{{ workspace.activeCodexGoalLoop?.max_iterations ?? 8 }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <CodexGoalLoopCard
      v-if="workspace.selectedCodexAdvancedMode === 'ralph-loop'"
      :state="workspace.activeCodexGoalLoop"
      :goal="workspace.codexGoalLoopDraft.goal"
      :definition-of-done="workspace.codexGoalLoopDraft.definitionOfDone"
      :saving="workspace.savingState.codexGoalLoop"
      @update:goal="workspace.codexGoalLoopDraft.goal = $event"
      @update:definition-of-done="workspace.codexGoalLoopDraft.definitionOfDone = $event"
      @save="workspace.saveCodexGoalLoop"
      @action="workspace.runCodexGoalLoopAction"
    />

    <p
      v-if="props.mobile"
      class="m-0 text-[0.78rem] leading-[1.5] text-[color:var(--app-text-soft)]"
    >
      The side button stays visible while Codex is running, so you can reopen this sheet anytime.
    </p>
  </section>
</template>

<script setup lang="ts">
import type { CodexWorkMode } from '../types/api'

const props = defineProps<{
  mode: CodexWorkMode
  disabled: boolean
}>()

const emit = defineEmits<{
  toggle: []
}>()

function onToggle() {
  if (!props.disabled) {
    emit('toggle')
  }
}
</script>

<template>
  <button
    type="button"
    class="composer-mode-chip inline-flex items-center gap-1 rounded-full border-0 bg-transparent px-1 py-0.5 text-left transition hover:bg-white/36"
    :disabled="disabled"
    aria-label="Toggle plan mode"
    @click="onToggle"
  >
    <i
      class="text-[0.72rem]"
      :class="mode === 'plan' ? 'pi pi-file-check text-[color:var(--app-accent)]' : 'pi pi-hammer text-[color:var(--app-text-soft)]'"
    ></i>
    <span
      class="truncate text-[0.78rem] font-medium tracking-[0.01em]"
      :class="mode === 'plan' ? 'text-[color:var(--app-accent)]' : 'text-[color:var(--app-text-soft)]'"
    >
      {{ mode === 'plan' ? 'Plan' : 'Build' }}
    </span>
    <i class="pi pi-chevron-down text-[0.6rem] text-[color:var(--app-text-soft)]"></i>
  </button>
</template>

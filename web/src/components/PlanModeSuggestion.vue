<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  composerText: string
  isPlanMode: boolean
}>()

const emit = defineEmits<{
  activatePlan: []
  dismiss: []
}>()

const isDismissed = ref(false)

const PLAN_KEYWORD_RE = /\bplan\b/i

const shouldShow = computed(() => {
  if (isDismissed.value || props.isPlanMode) return false
  return PLAN_KEYWORD_RE.test(props.composerText)
})

function onActivate() {
  emit('activatePlan')
}

function onDismiss() {
  isDismissed.value = true
  emit('dismiss')
}
</script>

<template>
  <div
    v-if="shouldShow"
    class="plan-suggestion flex items-center gap-2 rounded-xl border border-[rgba(21,94,99,0.12)] bg-[rgba(235,248,246,0.72)] px-3 py-2 text-[0.82rem] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.45)]"
  >
    <i class="pi pi-file-check text-[color:var(--app-accent)] text-[0.9rem]"></i>
    <span class="min-w-0 flex-1 text-[color:var(--app-text)]">
      Create a plan
    </span>
    <kbd class="pointer-events-none rounded-md border border-[rgba(34,66,72,0.1)] bg-white/60 px-1.5 py-0.5 text-[0.7rem] leading-none text-[color:var(--app-text-soft)]">
      Shift + Tab
    </kbd>
    <button
      type="button"
      class="shrink-0 rounded-full bg-[rgba(21,94,99,0.08)] px-2.5 py-1 text-[0.76rem] font-semibold text-[color:var(--app-accent-deep)] transition hover:bg-[rgba(21,94,99,0.16)]"
      @click="onActivate"
    >
      Use plan mode
    </button>
    <button
      type="button"
      class="shrink-0 rounded-full border-0 bg-transparent p-0.5 text-[color:var(--app-text-soft)] transition hover:bg-black/5 hover:text-[color:var(--app-text)]"
      aria-label="Dismiss suggestion"
      @click="onDismiss"
    >
      <i class="pi pi-times text-[0.68rem]"></i>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { isWorkingStatus } from '../lib/format'

const props = defineProps<{
  status?: string | null
  workStartedAtMs?: number | null
  turnStartedAtMs?: number | null
  finalAssistantStartedAtMs?: number | null
  durationMs?: number | null
}>()

const nowMs = ref(Date.now())
let timer: number | null = null

function coerceMs(value: number | null | undefined) {
  if (!value) {
    return null
  }
  return value > 10_000_000_000 ? value : value * 1000
}

function formatDuration(ms: number | null | undefined) {
  if (!ms || ms <= 0) {
    return ''
  }
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) {
    return `${seconds}s`
  }
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  if (minutes < 60) {
    return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`
  }
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`
}

const isWorking = computed(() => isWorkingStatus(props.status))

const elapsedMs = computed(() => {
  const workStartedAtMs = coerceMs(props.workStartedAtMs)
  const turnStartedAtMs = coerceMs(props.turnStartedAtMs)
  const finalAssistantStartedAtMs = coerceMs(props.finalAssistantStartedAtMs)
  if (isWorking.value) {
    const startedAtMs = workStartedAtMs ?? turnStartedAtMs
    return startedAtMs ? Math.max(nowMs.value - startedAtMs, 0) : null
  }
  const startedAtMs = workStartedAtMs ?? turnStartedAtMs
  if (startedAtMs && finalAssistantStartedAtMs) {
    return Math.max(finalAssistantStartedAtMs - startedAtMs, 0)
  }
  if (props.durationMs != null) {
    return Math.max(props.durationMs, 0)
  }
  return null
})

const label = computed(() => {
  const duration = formatDuration(elapsedMs.value)
  if (isWorking.value) {
    return duration ? `Working for ${duration}` : 'Working'
  }
  return duration ? `Worked for ${duration}` : 'Worked'
})

function syncTimer() {
  if (timer != null) {
    window.clearInterval(timer)
    timer = null
  }
  if (!isWorking.value) {
    return
  }
  nowMs.value = Date.now()
  timer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
}

onMounted(syncTimer)
onUnmounted(() => {
  if (timer != null) {
    window.clearInterval(timer)
  }
})

watch(isWorking, syncTimer)
</script>

<template>
  <span>{{ label }}</span>
</template>

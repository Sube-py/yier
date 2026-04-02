<script setup lang="ts">
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

import type { BackendRuntime } from '../../types/api'
import { runtimeStatusLabel } from './helpers'

defineProps<{
  compactHeader?: boolean
  isSending: boolean
  messageCount: number
  projectPath: string
  sessionLabel: string
  sessionRuntime: BackendRuntime | null
}>()
</script>

<template>
  <div
    v-if="compactHeader"
    class="flex items-center justify-between gap-3 rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.7)] px-3 py-2.5"
  >
    <div class="min-w-0">
      <p class="eyebrow">Working Directory</p>
      <p
        class="m-0 truncate font-mono text-[0.8rem] text-[color:var(--app-text-soft)]"
        :title="projectPath"
      >
        {{ projectPath || 'No project path' }}
      </p>
    </div>
    <div
      v-if="isSending"
      class="inline-flex shrink-0 items-center gap-2 text-[0.82rem] text-[color:var(--app-text-soft)]"
    >
      <ProgressSpinner
        stroke-width="4"
        class="h-[1rem] w-[1rem]"
      />
      <span>Working</span>
    </div>
  </div>

  <div
    v-else
    class="flex items-start justify-between gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch"
  >
    <div>
      <p class="eyebrow">Current session</p>
      <div class="flex items-center justify-start gap-3 max-sm:flex-wrap">
        <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-2xl font-semibold">
          Session {{ sessionLabel }}
        </h3>
        <Tag
          :value="messageCount ? `${messageCount} msgs` : 'New'"
          rounded
          severity="secondary"
        />
      </div>
      <p
        v-if="sessionRuntime"
        class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
      >
        {{ sessionRuntime.label }} · {{ runtimeStatusLabel(sessionRuntime.status) }}
        <span v-if="sessionRuntime.thread_id"> · {{ sessionRuntime.thread_id }}</span>
      </p>
      <p
        v-if="sessionRuntime?.detail"
        class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
      >
        {{ sessionRuntime.detail }}
      </p>
    </div>
    <div
      v-if="isSending"
      class="inline-flex items-center gap-2.5 self-start text-[color:var(--app-text-soft)]"
    >
      <ProgressSpinner
        stroke-width="4"
        class="h-[1.1rem] w-[1.1rem]"
      />
      <span>Working...</span>
    </div>
  </div>
</template>

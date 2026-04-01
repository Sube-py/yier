<script setup lang="ts">
import { computed } from 'vue'

import type { ActivityDisplayItem } from './types'
import {
  activitySummaryParts,
  fileChangeSummary,
  fileChangeVerb,
  fileChangeVerbClass,
  isShellActivity,
} from './helpers'

const props = withDefaults(
  defineProps<{
    display: ActivityDisplayItem
    isSending?: boolean
    size?: 'default' | 'compact'
  }>(),
  {
    isSending: false,
    size: 'default',
  },
)

const summary = computed(() => activitySummaryParts(props.display, props.isSending))
const changeSummary = computed(() => (props.display.change ? fileChangeSummary(props.display.change) : null))
const isCompact = computed(() => props.size === 'compact')
const shellWrapClass = computed(() =>
  isShellActivity(props.display.activity)
    ? 'overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none]'
    : '',
)
const bodyClass = computed(() =>
  isShellActivity(props.display.activity)
    ? 'min-w-full whitespace-nowrap font-mono'
    : 'flex-wrap',
)
const summaryTextClass = computed(() =>
  isCompact.value
    ? 'm-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.9rem] font-medium max-sm:text-[0.86rem]'
    : 'm-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.92rem] font-medium max-sm:text-[0.88rem]',
)
</script>

<template>
  <div
    class="min-w-0"
    :class="shellWrapClass"
  >
    <p
      v-if="display.activity.title"
      class="m-0 mb-1 text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]"
    >
      {{ display.activity.title }}
    </p>
    <p
      :class="[summaryTextClass, bodyClass]"
    >
      <template v-if="display.change && changeSummary">
        <span
          class="shrink-0"
          :class="fileChangeVerbClass(display.change)"
        >
          {{ fileChangeVerb(display.change) }}
        </span>
        <span class="min-w-0 break-words text-[color:var(--app-text)]">
          {{ changeSummary.label }}
        </span>
        <span
          v-if="changeSummary.additions > 0"
          class="inline-flex items-center rounded-full bg-[rgba(75,139,88,0.14)] px-2 py-0.5 text-[0.78rem] font-semibold text-[#4b8b58]"
        >
          +{{ changeSummary.additions }}
        </span>
        <span
          v-if="changeSummary.removals > 0"
          class="inline-flex items-center rounded-full bg-[rgba(184,93,72,0.14)] px-2 py-0.5 text-[0.78rem] font-semibold text-[#b85d48]"
        >
          -{{ changeSummary.removals }}
        </span>
      </template>

      <template v-else>
        <span
          class="shrink-0"
          :class="summary.verbClass"
        >
          {{ summary.verb }}
        </span>
        <span class="min-w-0 break-words text-[color:var(--app-text)]">
          {{ summary.text }}
        </span>
      </template>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { ApprovalDecision } from '../../types/api'
import type { ActivityDisplayItem } from './types'
import { hasActivityDetails } from './helpers'
import TimelineActivityDetail from './TimelineActivityDetail.vue'
import TimelineActivitySummary from './TimelineActivitySummary.vue'

const props = withDefaults(
  defineProps<{
    display: ActivityDisplayItem
    isOpen?: boolean
    isSending?: boolean
    onMarkdownClick: (event: MouseEvent) => void
    renderMarkdown: (content: string) => string
    variant: 'group' | 'standalone'
  }>(),
  {
    isOpen: false,
    isSending: false,
  },
)

const emit = defineEmits<{
  submitApproval: [{ requestId: string; decision: ApprovalDecision; contentText: string }]
  toggle: [event: Event]
}>()

const hasDetails = computed(() => hasActivityDetails(props.display))
const isStandalone = computed(() => props.variant === 'standalone')
const detailsClass = computed(() =>
  isStandalone.value
    ? 'activity-item overflow-hidden rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] shadow-[0_10px_30px_rgba(24,44,48,0.05)]'
    : 'activity-item overflow-hidden',
)
const summaryClass = computed(() =>
  isStandalone.value
    ? 'grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-3 px-4 py-3 max-sm:px-3 max-sm:py-2.5'
    : 'grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-3 py-1.5',
)
const summarySize = computed(() => (isStandalone.value ? 'default' : 'compact'))
const iconClass = computed(() => {
  if (!isStandalone.value) {
    return 'pi pi-angle-right text-[0.8rem] text-[color:var(--app-text-soft)]'
  }
  return [
    'pi pi-angle-down text-[0.82rem] text-[color:var(--app-text-soft)] transition-transform duration-150',
    props.isOpen ? 'rotate-180' : '',
  ]
})
const simpleClass = computed(() =>
  isStandalone.value
    ? 'activity-item rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.58)] px-4 py-3 max-sm:px-3 max-sm:py-2.5'
    : 'activity-item min-w-0 py-1.5',
)
</script>

<template>
  <details
    v-if="hasDetails"
    :class="detailsClass"
    :open="isStandalone ? isOpen : undefined"
    @toggle="emit('toggle', $event)"
  >
    <summary :class="summaryClass">
      <TimelineActivitySummary
        :display="display"
        :is-sending="isSending"
        :size="summarySize"
      />
      <i :class="iconClass"></i>
    </summary>

    <TimelineActivityDetail
      :display="display"
      :is-sending="isSending"
      :on-markdown-click="onMarkdownClick"
      :render-markdown="renderMarkdown"
      :variant="variant"
      @submit-approval="emit('submitApproval', $event)"
    />
  </details>

  <div
    v-else
    :class="simpleClass"
  >
    <TimelineActivitySummary
      :display="display"
      :is-sending="isSending"
      :size="summarySize"
    />
  </div>
</template>

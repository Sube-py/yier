<script setup lang="ts">
import type { ApprovalDecision, CodexTurnTiming } from '../../types/api'
import type { TurnGroupEntry } from './types'
import { turnGroupSummary } from './helpers'
import TimelineActivityItem from './TimelineActivityItem.vue'
import TimelineMessageItem from './TimelineMessageItem.vue'

defineProps<{
  group: TurnGroupEntry
  isOpen: boolean
  isSending: boolean
  onMarkdownClick: (event: MouseEvent) => void
  renderMarkdown: (content: string) => string
  turnTimings?: CodexTurnTiming[]
}>()

const emit = defineEmits<{
  submitApproval: [{ requestId: string; decision: ApprovalDecision; contentText: string }]
  toggle: [event: Event]
}>()
</script>

<template>
  <details
    class="overflow-hidden"
    :open="isOpen"
    @toggle="emit('toggle', $event)"
  >
    <summary
      class="flex cursor-pointer list-none items-center gap-3 py-1.5 text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--app-text-soft)]"
    >
      <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
      <span class="shrink-0">{{ turnGroupSummary(group, turnTimings) }}</span>
      <i
        class="pi pi-angle-right shrink-0 text-[0.78rem] transition-transform duration-150"
        :class="isOpen ? 'rotate-90' : ''"
      ></i>
      <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
    </summary>

    <div class="grid gap-3 pt-3">
      <template
        v-for="item in group.items"
        :key="item.key"
      >
        <TimelineMessageItem
          v-if="item.type === 'message'"
          :message="item.message"
          :on-markdown-click="onMarkdownClick"
          :render-markdown="renderMarkdown"
        />

        <TimelineActivityItem
          v-else
          :display="item.display"
          :is-sending="isSending"
          :on-markdown-click="onMarkdownClick"
          :render-markdown="renderMarkdown"
          variant="group"
          @submit-approval="emit('submitApproval', $event)"
        />
      </template>
    </div>
  </details>
</template>

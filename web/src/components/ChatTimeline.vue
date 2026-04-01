<script setup lang="ts">
import { computed, nextTick, onMounted, ref, toRef, watch } from 'vue'
import ScrollPanel from 'primevue/scrollpanel'

import { useTimelineFeed } from '../composables/useTimelineFeed'
import { useTimelineMarkdown } from '../composables/useTimelineMarkdown'

import type { ApprovalDecision, BackendRuntime, ChatActivity, CodexTurnTiming, UiChatMessage } from '../types/api'
import TimelineActivityItem from './chat-timeline/TimelineActivityItem.vue'
import TimelineHeader from './chat-timeline/TimelineHeader.vue'
import TimelineMessageItem from './chat-timeline/TimelineMessageItem.vue'
import TimelineTurnGroup from './chat-timeline/TimelineTurnGroup.vue'

const props = defineProps<{
  messages: UiChatMessage[]
  activities: ChatActivity[]
  turnTimings?: CodexTurnTiming[]
  isSending: boolean
  sessionLabel: string
  sessionRuntime: BackendRuntime | null
  projectPath: string
  assistantLabel?: string
  compactHeader?: boolean
  showReasoningCards?: boolean
}>()

const emit = defineEmits<{
  approvalAction: [requestId: string, decision: ApprovalDecision, contentText: string]
}>()

const timelineBody = ref<HTMLElement | null>(null)
const shouldStickToBottom = ref(true)
const bottomThreshold = 72

const timelineScrollPt = computed(() => ({
  contentContainer: {
    class: 'h-full w-full',
  },
  content: {
    class: 'flex min-h-0 flex-col gap-4 [overflow-x:clip] pr-[0.35rem]',
    ref: (element: HTMLElement | null) => {
      timelineBody.value = element
    },
    onScroll: onTimelineScroll,
  },
}))

const { renderMarkdown, onMarkdownClick } = useTimelineMarkdown()
const {
  renderEntries,
  isActivityOpen,
  isTurnGroupOpen,
  onActivityToggle,
  onTurnGroupToggle,
  shouldShowFinalMessageSeparator,
} = useTimelineFeed({
  messages: toRef(props, 'messages'),
  activities: toRef(props, 'activities'),
  turnTimings: toRef(props, 'turnTimings'),
  isSending: toRef(props, 'isSending'),
  showReasoningCards: toRef(props, 'showReasoningCards'),
})

function isNearBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= bottomThreshold
}

function onTimelineScroll() {
  if (!timelineBody.value) {
    return
  }
  shouldStickToBottom.value = isNearBottom(timelineBody.value)
}

async function scrollToBottomIfNeeded() {
  await nextTick()
  if (!timelineBody.value || !shouldStickToBottom.value) {
    return
  }
  timelineBody.value.scrollTop = timelineBody.value.scrollHeight
}

function forwardApproval(payload: { requestId: string; decision: ApprovalDecision; contentText: string }) {
  emit('approvalAction', payload.requestId, payload.decision, payload.contentText)
}

onMounted(async () => {
  await scrollToBottomIfNeeded()
})

watch(
  () => props.activities,
  async () => {
    await scrollToBottomIfNeeded()
  },
  { deep: true, flush: 'post' },
)

watch(
  () => [props.messages.length, props.isSending],
  async () => {
    await scrollToBottomIfNeeded()
  },
  { flush: 'post' },
)
</script>

<template>
  <section
    class="flex h-full min-h-0 flex-1 flex-col gap-4 overflow-hidden bg-transparent px-[1.1rem] pt-[1.1rem] pb-0 max-[1023px]:px-4 max-[1023px]:pt-4 max-[1023px]:pb-0 max-sm:gap-3 max-sm:px-3 max-sm:pt-3 max-sm:pb-0"
  >
    <TimelineHeader
      :compact-header="compactHeader"
      :is-sending="isSending"
      :message-count="messages.length"
      :project-path="projectPath"
      :session-label="sessionLabel"
      :session-runtime="sessionRuntime"
    />

    <div
      v-if="!renderEntries.length"
      class="grid place-items-center gap-2 p-10 text-center"
    >
      <p class="eyebrow">Ready</p>
      <h4 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-xl font-semibold">
        Start with a local task.
      </h4>
      <p class="m-0 max-w-[30rem] text-[color:var(--app-text-soft)]">
        Try asking yier to inspect this repo, summarize a file, or edit code inside the allowed
        roots.
      </p>
    </div>

    <ScrollPanel
      v-else
      class="min-h-0 flex-1"
      :pt="timelineScrollPt"
    >
      <div class="grid min-w-0 grid-cols-1 gap-3 [overflow-x:clip]">
        <template
          v-for="(entry, entryIndex) in renderEntries"
          :key="entry.key"
        >
          <TimelineMessageItem
            v-if="entry.type === 'message'"
            :message="entry.message"
            :on-markdown-click="onMarkdownClick"
            :render-markdown="renderMarkdown"
            :show-final-separator="shouldShowFinalMessageSeparator(entry, entryIndex)"
          />

          <TimelineTurnGroup
            v-else-if="entry.type === 'turn-group'"
            :group="entry"
            :is-open="isTurnGroupOpen(entry)"
            :is-sending="isSending"
            :on-markdown-click="onMarkdownClick"
            :render-markdown="renderMarkdown"
            :turn-timings="turnTimings"
            @submit-approval="forwardApproval"
            @toggle="onTurnGroupToggle(entry, $event)"
          />

          <TimelineActivityItem
            v-else
            :display="entry.display"
            :is-open="isActivityOpen(entry.display)"
            :is-sending="isSending"
            :on-markdown-click="onMarkdownClick"
            :render-markdown="renderMarkdown"
            variant="standalone"
            @submit-approval="forwardApproval"
            @toggle="onActivityToggle(entry.display, $event)"
          />
        </template>
      </div>
    </ScrollPanel>
  </section>
</template>

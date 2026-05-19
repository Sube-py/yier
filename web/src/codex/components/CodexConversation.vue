<script setup lang="ts">
import { computed } from 'vue'

import { useTimelineMarkdown } from '../../composables/useTimelineMarkdown'
import type { CodexConversationState, CodexTurnState, JsonRecord } from '../types'
import { compactJson, formatTimestamp, isRecord, statusLabel, textFromInput } from '../lib/format'

const props = defineProps<{
  state: CodexConversationState | null
}>()

const turns = computed<CodexTurnState[]>(() =>
  Array.isArray(props.state?.turns) ? props.state.turns : [],
)
const { renderMarkdown, onMarkdownClick } = useTimelineMarkdown()

function itemType(item: JsonRecord) {
  return typeof item.type === 'string' && item.type ? item.type : 'unknown'
}

function itemId(item: JsonRecord, index: number) {
  return typeof item.id === 'string' && item.id ? item.id : `${itemType(item)}-${index}`
}

function itemTitle(item: JsonRecord) {
  switch (itemType(item)) {
    case 'userMessage':
      return 'You'
    case 'steeringUserMessage':
      return 'Steer'
    case 'agentMessage':
      return 'Codex'
    case 'plan':
      return 'Plan'
    case 'reasoning':
      return 'Reasoning'
    case 'commandExecution':
      return 'Command'
    case 'fileChange':
      return 'Files'
    case 'dynamicToolCall':
      return 'Tool'
    case 'mcpToolCall':
      return 'MCP'
    case 'collabAgentToolCall':
      return 'Agent'
    case 'userInputResponse':
      return 'User input'
    case 'contextCompaction':
      return 'Compaction'
    default:
      return itemType(item)
  }
}

function itemTone(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'userMessage' || type === 'steeringUserMessage') {
    return 'justify-end'
  }
  return 'justify-start'
}

function bubbleWidth(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'userMessage' || type === 'steeringUserMessage') {
    return 'w-fit max-w-[min(40rem,88%)]'
  }
  return 'w-full max-w-[min(52rem,100%)]'
}

function bubbleTone(item: JsonRecord) {
  switch (itemType(item)) {
    case 'userMessage':
      return 'border-[rgba(21,94,99,0.18)] bg-[rgba(21,94,99,0.08)]'
    case 'steeringUserMessage':
      return 'border-[rgba(37,99,235,0.18)] bg-blue-50'
    case 'agentMessage':
      return 'border-[color:var(--app-border)] bg-white'
    case 'plan':
      return 'border-[rgba(21,94,99,0.18)] bg-emerald-50'
    case 'reasoning':
      return 'border-[rgba(99,102,241,0.18)] bg-indigo-50'
    case 'commandExecution':
    case 'dynamicToolCall':
    case 'mcpToolCall':
    case 'collabAgentToolCall':
      return 'border-[rgba(136,109,67,0.18)] bg-amber-50'
    case 'fileChange':
      return 'border-[rgba(22,101,52,0.18)] bg-green-50'
    case 'userInputResponse':
      return 'border-[rgba(37,99,235,0.18)] bg-blue-50'
    default:
      return 'border-[color:var(--app-border)] bg-[rgba(255,255,255,0.72)]'
  }
}

function outputText(value: unknown) {
  const text = textFromInput(value)
  return text.trim()
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }
  return ''
}

function itemText(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'userMessage') {
    return outputText(item.content || item.input)
  }
  if (type === 'steeringUserMessage') {
    return outputText(item.input)
  }
  if (type === 'agentMessage') {
    return firstString(item.text, outputText(item.content))
  }
  if (type === 'plan') {
    return firstString(item.text)
  }
  if (type === 'reasoning') {
    return firstString(outputText(item.summary), outputText(item.content), item.text)
  }
  if (type === 'commandExecution') {
    return [firstString(item.command), firstString(item.aggregatedOutput, outputText(item.output))]
      .filter(Boolean)
      .join('\n\n')
  }
  if (type === 'dynamicToolCall') {
    return [
      firstString(item.tool, item.name),
      firstString(item.status),
      firstString(item.aggregatedOutput, outputText(item.contentItems)),
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (type === 'mcpToolCall') {
    return [
      firstString(item.server, item.serverName),
      firstString(item.tool, item.name),
      firstString(item.status),
      firstString(item.aggregatedOutput, outputText(item.result)),
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (type === 'collabAgentToolCall') {
    return [firstString(item.tool, item.name), firstString(item.status), firstString(item.prompt)]
      .filter(Boolean)
      .join('\n')
  }
  if (type === 'fileChange') {
    const changes = item.changes
    if (Array.isArray(changes)) {
      return changes
        .map((change) => (isRecord(change) && typeof change.path === 'string' ? change.path : ''))
        .filter(Boolean)
        .join('\n')
    }
    if (isRecord(changes)) {
      return Object.keys(changes).join('\n')
    }
  }
  if (type === 'userInputResponse') {
    return compactJson(item.answers ?? {})
  }
  if (type === 'contextCompaction') {
    return 'Context compacted.'
  }
  return firstString(item.text, outputText(item.content), outputText(item.input))
}

function shouldShowJson(item: JsonRecord) {
  const knownTypes = new Set([
    'userMessage',
    'steeringUserMessage',
    'agentMessage',
    'plan',
    'reasoning',
    'commandExecution',
    'fileChange',
    'dynamicToolCall',
    'mcpToolCall',
    'collabAgentToolCall',
    'userInputResponse',
    'contextCompaction',
  ])
  return !knownTypes.has(itemType(item)) || !itemText(item)
}

function shouldRenderMarkdown(item: JsonRecord) {
  return (
    ['userMessage', 'steeringUserMessage', 'agentMessage', 'plan', 'reasoning'].includes(
      itemType(item),
    ) && Boolean(itemText(item))
  )
}

function shouldRenderRawText(item: JsonRecord) {
  return Boolean(itemText(item)) && !shouldRenderMarkdown(item) && !shouldShowJson(item)
}

function turnKey(turn: CodexTurnState, index: number) {
  return turn.turnId || `turn-${index}`
}
</script>

<template>
  <section class="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-clip bg-[rgba(255,253,247,0.38)] px-5 py-4 max-sm:px-3">
    <div v-if="!state" class="grid h-full min-h-72 place-items-center text-center text-[color:var(--app-text-soft)]">
      <div class="grid gap-2">
        <i class="pi pi-comments text-xl"></i>
        <p class="m-0 text-sm">Select or start a thread.</p>
      </div>
    </div>

    <div v-else-if="!turns.length" class="grid h-full min-h-72 place-items-center text-center text-[color:var(--app-text-soft)]">
      <div class="grid gap-2">
        <i class="pi pi-sparkles text-xl"></i>
        <p class="m-0 text-sm">Ready for a prompt.</p>
      </div>
    </div>

    <div v-else class="mx-auto grid w-full max-w-5xl min-w-0 gap-5 overflow-x-clip">
      <section
        v-for="(turn, turnIndex) in turns"
        :key="turnKey(turn, turnIndex)"
        class="grid min-w-0 gap-3 overflow-x-clip border-b border-[color:var(--app-border)] pb-5 last:border-b-0"
      >
        <div class="flex min-w-0 flex-wrap items-center gap-2 text-[0.74rem] text-[color:var(--app-text-soft)]">
          <span class="font-semibold text-[color:var(--app-text)]">
            Turn {{ turnIndex + 1 }}
          </span>
          <span>{{ statusLabel(turn.status) }}</span>
          <code v-if="turn.turnId" class="truncate">{{ turn.turnId }}</code>
          <span v-if="turn.turnStartedAtMs">{{ formatTimestamp(turn.turnStartedAtMs) }}</span>
        </div>

        <div class="grid min-w-0 gap-3 overflow-x-clip">
          <article
            v-for="(item, itemIndex) in (Array.isArray(turn.items) ? turn.items : [])"
            :key="itemId(item, itemIndex)"
            class="flex min-w-0 w-full"
            :class="itemTone(item)"
          >
            <div
              class="min-w-0 overflow-hidden rounded-xl border px-3.5 py-3 shadow-[0_10px_28px_rgba(24,44,48,0.05)]"
              :class="[bubbleTone(item), bubbleWidth(item)]"
              data-codex-bubble
            >
              <div class="mb-1.5 flex min-w-0 items-center justify-between gap-3">
                <p class="m-0 truncate text-[0.76rem] font-bold uppercase tracking-[0.1em] text-[color:var(--app-text-soft)]">
                  {{ itemTitle(item) }}
                </p>
                <span
                  v-if="typeof item.status === 'string' && item.status"
                  class="shrink-0 rounded-full bg-white/72 px-2 py-0.5 text-[0.68rem] font-semibold text-[color:var(--app-text-soft)]"
                >
                  {{ statusLabel(item.status) }}
                </span>
              </div>
              <div
                v-if="shouldRenderMarkdown(item)"
                class="markdown-prose min-w-0 [overflow-wrap:anywhere] [&>:first-child]:mt-0 [&>:last-child]:mb-0"
                v-html="renderMarkdown(itemText(item))"
                @click="onMarkdownClick"
              ></div>
              <pre
                v-if="shouldRenderRawText(item)"
                class="m-0 max-h-80 max-w-full overflow-auto overscroll-contain rounded-lg border border-[rgba(34,66,72,0.08)] bg-white/78 p-3 text-xs leading-5 text-[color:var(--app-text-soft)]"
                data-codex-raw
              >{{ itemText(item) }}</pre>
              <pre
                v-if="shouldShowJson(item)"
                class="m-0 max-h-80 max-w-full overflow-auto overscroll-contain rounded-lg border border-[rgba(34,66,72,0.08)] bg-white/78 p-3 text-xs leading-5 text-[color:var(--app-text-soft)]"
                data-codex-raw
              >{{ compactJson(item) }}</pre>
            </div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import type { CodexConversationState, CodexTurnState, JsonRecord } from '../types'
import { compactJson, formatTimestamp, isRecord, statusLabel, textFromInput } from '../lib/format'
import { useCodexMarkdown } from '../lib/markdown'

const props = defineProps<{
  state: CodexConversationState | null
}>()

type ConversationItemKind = 'user' | 'assistant' | 'work' | 'unknown'

interface ConversationItemView {
  id: string
  item: JsonRecord
  kind: ConversationItemKind
}

interface TurnView {
  key: string
  turn: CodexTurnState
  userItems: ConversationItemView[]
  workItems: ConversationItemView[]
  responseItems: ConversationItemView[]
  unknownItems: ConversationItemView[]
  summary: string
}

const workItemTypes = new Set([
  'reasoning',
  'commandExecution',
  'fileChange',
  'dynamicToolCall',
  'mcpToolCall',
  'collabAgentToolCall',
  'userInputResponse',
  'contextCompaction',
])

const responseItemTypes = new Set(['agentMessage', 'plan'])
const userItemTypes = new Set(['userMessage', 'steeringUserMessage'])

const turns = computed<CodexTurnState[]>(() =>
  Array.isArray(props.state?.turns) ? props.state.turns : [],
)
const turnViews = computed<TurnView[]>(() =>
  turns.value.map((turn, index) => {
    const userItems: ConversationItemView[] = []
    const workItems: ConversationItemView[] = []
    const responseItems: ConversationItemView[] = []
    const unknownItems: ConversationItemView[] = []

    for (const [itemIndex, item] of (Array.isArray(turn.items) ? turn.items : []).entries()) {
      const view = {
        id: itemId(item, itemIndex),
        item,
        kind: itemKind(item),
      }

      if (view.kind === 'user') {
        userItems.push(view)
      } else if (view.kind === 'assistant') {
        responseItems.push(view)
      } else if (view.kind === 'work') {
        workItems.push(view)
      } else {
        unknownItems.push(view)
      }
    }

    return {
      key: turnKey(turn, index),
      turn,
      userItems,
      workItems,
      responseItems,
      unknownItems,
      summary: finalTurnSummary(workItems.map((workItem) => workItem.item)),
    }
  }),
)
const { renderMarkdown, onMarkdownClick } = useCodexMarkdown()
const conversationBody = ref<HTMLElement | null>(null)
const shouldStickToBottom = ref(true)
const expandedWorkByTurnKey = ref<Record<string, boolean>>({})
const expandedItemById = ref<Record<string, boolean>>({})
const nowMs = ref(Date.now())
const bottomThreshold = 72
let nowTimer: number | null = null

function isNearBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= bottomThreshold
}

function onConversationScroll() {
  if (!conversationBody.value) {
    return
  }
  shouldStickToBottom.value = isNearBottom(conversationBody.value)
}

async function scrollToBottomIfNeeded() {
  await nextTick()
  if (!conversationBody.value || !shouldStickToBottom.value) {
    return
  }
  conversationBody.value.scrollTop = conversationBody.value.scrollHeight
}

async function resetBottomStickiness() {
  shouldStickToBottom.value = true
  expandedWorkByTurnKey.value = {}
  expandedItemById.value = {}
  await scrollToBottomIfNeeded()
}

function itemKind(item: JsonRecord): ConversationItemKind {
  const type = itemType(item)
  if (userItemTypes.has(type)) {
    return 'user'
  }
  if (responseItemTypes.has(type)) {
    return 'assistant'
  }
  if (workItemTypes.has(type)) {
    return 'work'
  }
  return 'unknown'
}

function itemType(item: JsonRecord) {
  return typeof item.type === 'string' && item.type ? item.type : 'unknown'
}

function itemId(item: JsonRecord, index: number) {
  return typeof item.id === 'string' && item.id ? item.id : `${itemType(item)}-${index}`
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

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value
    }
  }
  return null
}

function coerceMs(value: number | null | undefined) {
  if (!value) {
    return null
  }
  return value > 10_000_000_000 ? value : value * 1000
}

function formatDuration(ms: number | null | undefined) {
  if (!ms || ms < 1000) {
    return ''
  }
  const seconds = Math.max(1, Math.round(ms / 1000))
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

function turnElapsedMs(turn: CodexTurnState) {
  const durationMs = firstNumber(turn.durationMs)
  if (durationMs != null) {
    return Math.max(durationMs, 0)
  }

  const startedAtMs = coerceMs(turn.turnStartedAtMs)
  if (!startedAtMs) {
    return null
  }

  const finalAssistantStartedAtMs = coerceMs(turn.finalAssistantStartedAtMs)
  if (finalAssistantStartedAtMs) {
    return Math.max(finalAssistantStartedAtMs - startedAtMs, 0)
  }

  if (isTurnInProgress(turn)) {
    return Math.max(nowMs.value - startedAtMs, 0)
  }

  return null
}

function workedLabel(turn: CodexTurnState) {
  const duration = formatDuration(turnElapsedMs(turn))
  if (isTurnInProgress(turn)) {
    return duration ? `Working for ${duration}` : 'Working'
  }
  return duration ? `Worked for ${duration}` : 'Worked'
}

function isTurnInProgress(turn: CodexTurnState) {
  return turn.status === 'inProgress' || turn.status === 'active' || turn.status === 'working'
}

function isItemInProgress(item: JsonRecord) {
  if (item.completed === false) {
    return true
  }
  const status = firstString(item.status, item.executionStatus).toLowerCase()
  return ['active', 'inprogress', 'in_progress', 'running', 'working'].includes(status)
}

function isWorkExpanded(turnView: TurnView) {
  const override = expandedWorkByTurnKey.value[turnView.key]
  return override ?? isTurnInProgress(turnView.turn)
}

function toggleWork(turnView: TurnView) {
  expandedWorkByTurnKey.value = {
    ...expandedWorkByTurnKey.value,
    [turnView.key]: !isWorkExpanded(turnView),
  }
}

function isItemExpanded(itemId: string) {
  return expandedItemById.value[itemId] ?? false
}

function toggleItem(itemId: string) {
  expandedItemById.value = {
    ...expandedItemById.value,
    [itemId]: !isItemExpanded(itemId),
  }
}

function itemText(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'userMessage') {
    return outputText(item.content || item.input)
  }
  if (type === 'steeringUserMessage') {
    return outputText(item.input || item.content)
  }
  if (type === 'agentMessage') {
    return firstString(item.text, outputText(item.content))
  }
  if (type === 'plan') {
    return firstString(item.text, item.content)
  }
  if (type === 'reasoning') {
    return firstString(outputText(item.summary), outputText(item.content), item.text)
  }
  if (type === 'commandExecution') {
    return commandOutput(item)
  }
  if (type === 'dynamicToolCall') {
    return firstString(item.aggregatedOutput, outputText(item.contentItems), outputText(item.result))
  }
  if (type === 'mcpToolCall') {
    return firstString(item.aggregatedOutput, outputText(item.result), outputText(item.contentItems))
  }
  if (type === 'collabAgentToolCall') {
    return firstString(item.prompt, item.aggregatedOutput, outputText(item.result))
  }
  if (type === 'fileChange') {
    return fileChangePaths(item).join('\n')
  }
  if (type === 'userInputResponse') {
    return compactJson(item.answers ?? {})
  }
  if (type === 'contextCompaction') {
    return 'Context compacted.'
  }
  return firstString(item.text, outputText(item.content), outputText(item.input))
}

function commandText(item: JsonRecord) {
  return cleanShellCommand(firstString(item.command, item.cmd))
}

function commandOutput(item: JsonRecord) {
  const output = item.output
  if (isRecord(output)) {
    return firstString(output.aggregatedOutput, output.stdout, output.stderr, output.text)
  }
  return firstString(item.aggregatedOutput, outputText(output))
}

function commandExitCode(item: JsonRecord) {
  const output = item.output
  if (isRecord(output)) {
    return firstNumber(output.exitCode, output.exit_code)
  }
  return firstNumber(item.exitCode, item.exit_code)
}

function commandSucceeded(item: JsonRecord) {
  const exitCode = commandExitCode(item)
  return exitCode === 0 || item.success === true
}

function cleanShellCommand(command: string) {
  let text = command.trim().replace(/^\$\s+/, '')
  const shellMatch = text.match(/^(?:\/bin\/)?(?:zsh|bash|sh)\s+-lc\s+([\s\S]+)$/)
  if (shellMatch?.[1]) {
    text = shellMatch[1].trim()
  }
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    text = text.slice(1, -1)
  }
  return text.replace(/\\"/g, '"').replace(/\\'/g, "'").trim()
}

function fileChangePaths(item: JsonRecord) {
  const changes = item.changes
  if (Array.isArray(changes)) {
    return changes
      .map((change) => (isRecord(change) && typeof change.path === 'string' ? change.path : ''))
      .filter(Boolean)
  }
  if (isRecord(changes)) {
    return Object.keys(changes)
  }
  return []
}

function toolName(item: JsonRecord) {
  return firstString(item.tool, item.name, item.serverName, item.server) || itemType(item)
}

function humanizeName(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

function workItemTitle(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'reasoning') {
    const duration = formatDuration(turnElapsedForItem(item))
    if (isItemInProgress(item)) {
      return 'Thinking'
    }
    return duration ? `Thought for ${duration}` : 'Thought'
  }
  if (type === 'commandExecution') {
    return isItemInProgress(item) ? 'Running command' : 'Ran command'
  }
  if (type === 'fileChange') {
    const count = fileChangePaths(item).length
    return count === 1 ? 'Changed 1 file' : `Changed ${count} files`
  }
  if (type === 'dynamicToolCall') {
    return `${isItemInProgress(item) ? 'Calling' : 'Called'} ${humanizeName(toolName(item))}`
  }
  if (type === 'mcpToolCall') {
    const server = firstString(item.server, item.serverName)
    const name = humanizeName(toolName(item))
    return `${isItemInProgress(item) ? 'Calling' : 'Called'} ${server ? `${server} / ${name}` : name}`
  }
  if (type === 'collabAgentToolCall') {
    return `${isItemInProgress(item) ? 'Starting' : 'Used'} ${humanizeName(toolName(item))}`
  }
  if (type === 'userInputResponse') {
    return 'Answered request'
  }
  if (type === 'contextCompaction') {
    return 'Compacted context'
  }
  return humanizeName(type)
}

function workItemIcon(item: JsonRecord) {
  switch (itemType(item)) {
    case 'reasoning':
      return 'pi pi-sparkles'
    case 'commandExecution':
      return 'pi pi-terminal'
    case 'fileChange':
      return 'pi pi-file-edit'
    case 'mcpToolCall':
    case 'dynamicToolCall':
      return 'pi pi-wrench'
    case 'collabAgentToolCall':
      return 'pi pi-users'
    case 'userInputResponse':
      return 'pi pi-reply'
    case 'contextCompaction':
      return 'pi pi-compress'
    default:
      return 'pi pi-circle'
  }
}

function turnElapsedForItem(item: JsonRecord) {
  const startedAtMs = coerceMs(firstNumber(item.startedAtMs, item.started_at_ms))
  const completedAtMs = coerceMs(firstNumber(item.completedAtMs, item.completed_at_ms))
  if (startedAtMs && completedAtMs) {
    return Math.max(completedAtMs - startedAtMs, 0)
  }
  if (startedAtMs && isItemInProgress(item)) {
    return Math.max(nowMs.value - startedAtMs, 0)
  }
  return firstNumber(item.durationMs, item.duration_ms)
}

function workItemDetail(item: JsonRecord) {
  const type = itemType(item)
  if (type === 'commandExecution') {
    return commandOutput(item)
  }
  if (type === 'fileChange') {
    return fileChangePaths(item).join('\n')
  }
  return itemText(item)
}

function finalTurnSummary(items: JsonRecord[]) {
  const changedPaths = new Set<string>()
  let linesAdded = 0
  let linesRemoved = 0
  let commandCount = 0
  let toolCount = 0

  for (const item of items) {
    const type = itemType(item)
    if (type === 'commandExecution') {
      commandCount += 1
    }
    if (['dynamicToolCall', 'mcpToolCall', 'collabAgentToolCall'].includes(type)) {
      toolCount += 1
    }
    if (type === 'fileChange') {
      for (const path of fileChangePaths(item)) {
        changedPaths.add(path)
      }
      const changes = item.changes
      const changeItems = Array.isArray(changes)
        ? changes
        : isRecord(changes)
          ? Object.values(changes)
          : []
      for (const change of changeItems) {
        if (!isRecord(change)) {
          continue
        }
        linesAdded += firstNumber(change.linesAdded, change.added, change.additions) ?? 0
        linesRemoved += firstNumber(change.linesRemoved, change.removed, change.deletions) ?? 0
      }
    }
  }

  const parts: string[] = []
  if (changedPaths.size > 0) {
    parts.push(
      `${changedPaths.size} ${changedPaths.size === 1 ? 'file' : 'files'} changed`,
    )
  }
  if (linesAdded || linesRemoved) {
    const lineParts = []
    if (linesAdded) {
      lineParts.push(`+${linesAdded}`)
    }
    if (linesRemoved) {
      lineParts.push(`-${linesRemoved}`)
    }
    parts.push(lineParts.join(' / '))
  }
  if (commandCount > 0) {
    parts.push(`${commandCount} ${commandCount === 1 ? 'command' : 'commands'}`)
  }
  if (toolCount > 0) {
    parts.push(`${toolCount} ${toolCount === 1 ? 'tool call' : 'tool calls'}`)
  }

  return parts.join(' · ')
}

function shouldRenderMarkdown(item: JsonRecord) {
  return ['userMessage', 'steeringUserMessage', 'agentMessage', 'plan'].includes(itemType(item))
}

function shouldShowUnknownJson(item: JsonRecord) {
  return itemKind(item) === 'unknown' || !itemText(item)
}

function responseTone(item: JsonRecord) {
  return itemType(item) === 'plan'
    ? 'border-l-[3px] border-emerald-300/70 pl-3'
    : 'border-l-[3px] border-transparent pl-3'
}

function turnKey(turn: CodexTurnState, index: number) {
  return turn.turnId || `turn-${index}`
}

onMounted(async () => {
  nowTimer = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
  await scrollToBottomIfNeeded()
})

onUnmounted(() => {
  if (nowTimer != null) {
    window.clearInterval(nowTimer)
  }
})

watch(
  () => props.state?.id,
  async () => {
    await resetBottomStickiness()
  },
  { flush: 'post' },
)

watch(
  () => props.state?.turns,
  async () => {
    await scrollToBottomIfNeeded()
  },
  { deep: true, flush: 'post' },
)

const justDebug = false
</script>

<template>
  <section
    ref="conversationBody"
    class="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-clip bg-[rgba(255,253,247,0.38)] px-5 py-4 max-sm:px-2.5"
    data-codex-conversation-body
    @scroll="onConversationScroll"
  >
    <div
      v-if="!state"
      class="grid h-full min-h-72 place-items-center text-center text-[color:var(--app-text-soft)]"
    >
      <div class="grid gap-2">
        <i class="pi pi-comments text-xl"></i>
        <p class="m-0 text-sm">Select or start a thread.</p>
      </div>
    </div>

    <div
      v-else-if="!turnViews.length"
      class="grid h-full min-h-72 place-items-center text-center text-[color:var(--app-text-soft)]"
    >
      <div class="grid gap-2">
        <i class="pi pi-sparkles text-xl"></i>
        <p class="m-0 text-sm">Ready for a prompt.</p>
      </div>
    </div>

    <div
      v-else
      class="mx-auto grid w-full max-w-5xl min-w-0 gap-6 overflow-x-clip max-sm:gap-5"
    >
      <section
        v-for="(turnView, turnIndex) in turnViews"
        :key="turnView.key"
        class="grid min-w-0 gap-3 overflow-x-clip border-b border-[color:var(--app-border)] pb-6 last:border-b-0"
        data-codex-turn
      >
        <div class="flex min-w-0 flex-wrap items-center gap-2 text-[0.74rem] text-[color:var(--app-text-soft)]">
          <span class="font-semibold text-[color:var(--app-text)]">
            Turn {{ turnIndex + 1 }}
          </span>
          <span v-if="justDebug">{{ statusLabel(turnView.turn.status) }}</span>
          <code
            v-if="justDebug && turnView.turn.turnId"
            class="truncate"
          >{{ turnView.turn.turnId }}</code>
          <span v-if="turnView.turn.turnStartedAtMs">
            {{ formatTimestamp(turnView.turn.turnStartedAtMs) }}
          </span>
        </div>

        <article
          v-for="userItem in turnView.userItems"
          :key="userItem.id"
          class="flex min-w-0 w-full justify-end"
          data-codex-user-message
        >
          <div
            class="min-w-0 w-fit max-w-[min(40rem,88%)] overflow-hidden rounded-2xl border border-[rgba(21,94,99,0.18)] bg-[rgba(21,94,99,0.08)] px-3.5 py-2.5 shadow-[0_10px_28px_rgba(24,44,48,0.04)] max-sm:max-w-[96%]"
            data-codex-bubble
          >
            <div
              class="markdown-prose markdown-prose-compact min-w-0 [overflow-wrap:anywhere] [&>:first-child]:mt-0 [&>:last-child]:mb-0"
              v-html="renderMarkdown(itemText(userItem.item))"
              @click="onMarkdownClick"
            ></div>
          </div>
        </article>

        <div
          v-if="turnView.workItems.length"
          class="grid min-w-0 gap-2"
          data-codex-work-section
        >
          <div class="flex min-w-0 items-center gap-2 text-sm text-[color:var(--app-text-soft)]">
            <button
              type="button"
              class="group inline-flex max-w-full shrink-0 items-center gap-1.5 rounded-md border border-transparent px-1 py-0.5 text-left transition hover:bg-white/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(21,94,99,0.18)]"
              :aria-expanded="isWorkExpanded(turnView)"
              data-codex-work-toggle
              @click="toggleWork(turnView)"
            >
              <span class="truncate">{{ workedLabel(turnView.turn) }}</span>
              <i
                class="pi pi-chevron-right text-[0.65rem] opacity-50 transition-transform"
                :class="isWorkExpanded(turnView) ? 'rotate-90' : ''"
              ></i>
            </button>
            <span class="h-px min-w-0 flex-1 bg-[rgba(34,66,72,0.14)]"></span>
          </div>

          <div
            v-if="isWorkExpanded(turnView)"
            class="grid min-w-0 gap-1.5 border-l border-[rgba(34,66,72,0.12)] pl-3 max-sm:pl-2"
            data-codex-work-items
          >
            <div
              v-for="workItem in turnView.workItems"
              :key="workItem.id"
              class="grid min-w-0 gap-1 rounded-lg px-1.5 py-1 text-sm text-[color:var(--app-text-soft)] transition hover:bg-white/55"
              data-codex-work-item
            >
              <button
                type="button"
                class="group flex min-w-0 items-start gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(21,94,99,0.18)]"
                :aria-expanded="isItemExpanded(workItem.id)"
                @click="toggleItem(workItem.id)"
              >
                <i
                  class="mt-0.5 shrink-0 text-[0.82rem] text-[color:var(--app-text-soft)] opacity-75"
                  :class="workItemIcon(workItem.item)"
                ></i>
                <span class="min-w-0 flex-1 truncate text-[color:var(--app-text-soft)]">
                  {{ workItemTitle(workItem.item) }}
                </span>
                <span
                  v-if="itemType(workItem.item) === 'commandExecution' && commandText(workItem.item)"
                  class="hidden min-w-0 max-w-[44%] truncate font-mono text-xs opacity-70 sm:inline"
                >
                  $ {{ commandText(workItem.item) }}
                </span>
                <span
                  v-if="itemType(workItem.item) === 'commandExecution' && !isItemInProgress(workItem.item)"
                  class="shrink-0 text-xs opacity-70"
                >
                  {{ commandSucceeded(workItem.item) ? 'Success' : `Exit ${commandExitCode(workItem.item) ?? 'unknown'}` }}
                </span>
                <i
                  class="pi pi-chevron-right mt-1 shrink-0 text-[0.58rem] opacity-0 transition-all group-hover:opacity-50"
                  :class="isItemExpanded(workItem.id) ? 'rotate-90 opacity-50' : ''"
                ></i>
              </button>

              <div
                v-if="isItemExpanded(workItem.id)"
                class="min-w-0 pl-6 max-sm:pl-0"
                data-codex-work-detail
              >
                <div
                  v-if="itemType(workItem.item) === 'commandExecution'"
                  class="code-surface code-surface-compact min-w-0"
                  data-codex-command-output
                >
                  <div class="code-surface-toolbar">
                    <div class="code-surface-toolbar-meta">
                      <span class="code-surface-label">Shell</span>
                      <span
                        v-if="commandText(workItem.item)"
                        class="code-surface-runtime"
                      >
                        $ {{ commandText(workItem.item) }}
                      </span>
                    </div>
                  </div>
                  <div class="code-surface-scroll code-surface-scroll-compact">
                    <pre><code>{{ workItemDetail(workItem.item) || 'No output' }}</code></pre>
                  </div>
                </div>

                <pre
                  v-else-if="workItemDetail(workItem.item)"
                  class="m-0 max-h-56 max-w-full overflow-auto overscroll-contain rounded-lg border border-[rgba(34,66,72,0.08)] bg-white/75 p-2.5 text-xs leading-5 text-[color:var(--app-text-soft)]"
                  data-codex-raw
                >{{ workItemDetail(workItem.item) }}</pre>

                <pre
                  v-else
                  class="m-0 max-h-56 max-w-full overflow-auto overscroll-contain rounded-lg border border-[rgba(34,66,72,0.08)] bg-white/75 p-2.5 text-xs leading-5 text-[color:var(--app-text-soft)]"
                  data-codex-raw
                >{{ compactJson(workItem.item) }}</pre>
              </div>
            </div>
          </div>
        </div>

        <article
          v-for="responseItem in turnView.responseItems"
          :key="responseItem.id"
          class="min-w-0 max-w-[min(52rem,100%)]"
          :class="responseTone(responseItem.item)"
          data-codex-assistant-message
        >
          <div
            v-if="shouldRenderMarkdown(responseItem.item)"
            class="markdown-prose markdown-prose-conversation min-w-0 [overflow-wrap:anywhere] [&>:first-child]:mt-0 [&>:last-child]:mb-0"
            v-html="renderMarkdown(itemText(responseItem.item))"
            @click="onMarkdownClick"
          ></div>
        </article>

        <div
          v-if="turnView.responseItems.length && turnView.summary"
          class="flex min-w-0 items-center gap-2 text-xs font-semibold text-[color:var(--app-text-soft)]"
          data-codex-turn-summary
        >
          <i class="pi pi-check-circle text-[0.72rem] text-emerald-600"></i>
          <span class="min-w-0 truncate">{{ turnView.summary }}</span>
        </div>

        <article
          v-for="unknownItem in turnView.unknownItems"
          :key="unknownItem.id"
          class="min-w-0 max-w-[min(52rem,100%)] rounded-xl border border-[color:var(--app-border)] bg-white/70 p-3"
          data-codex-unknown-item
        >
          <pre
            v-if="shouldShowUnknownJson(unknownItem.item)"
            class="m-0 max-h-80 max-w-full overflow-auto overscroll-contain rounded-lg border border-[rgba(34,66,72,0.08)] bg-white/78 p-3 text-xs leading-5 text-[color:var(--app-text-soft)]"
            data-codex-raw
          >{{ compactJson(unknownItem.item) }}</pre>
        </article>
      </section>
    </div>
  </section>
</template>

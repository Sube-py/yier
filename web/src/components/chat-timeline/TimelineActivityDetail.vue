<script setup lang="ts">
import { computed } from 'vue'

import HighlightedCodeBlock from '../HighlightedCodeBlock.vue'

import type { ApprovalDecision, ChatActivity } from '../../types/api'
import type { ActivityDisplayItem } from './types'
import {
  activitySummaryText,
  activityUsesMarkdown,
  fileChangeKindLabel,
  fileChangeMetaLabel,
  fileChangeSummary,
  fileChangeVerbClass,
  genericActivityDetail,
  hasShellTranscript,
  isApprovalActivity,
  isShellActivity,
  shellCommand,
  shellOutputTranscript,
  shellRuntime,
} from './helpers'
import TimelineApprovalSection from './TimelineApprovalSection.vue'

const props = defineProps<{
  display: ActivityDisplayItem
  isSending: boolean
  onMarkdownClick: (event: MouseEvent) => void
  renderMarkdown: (content: string) => string
  variant: 'group' | 'standalone'
}>()

const emit = defineEmits<{
  submitApproval: [{ requestId: string; decision: ApprovalDecision; contentText: string }]
}>()

const changeSummary = computed(() => (props.display.change ? fileChangeSummary(props.display.change) : null))
const detailFrameClass = computed(() =>
  props.variant === 'group'
    ? 'border-l border-[rgba(34,66,72,0.08)] pl-4'
    : 'border-t border-[rgba(34,66,72,0.08)] px-4 pb-4 pt-3 max-sm:px-3 max-sm:pb-3',
)
const shellSectionClass = computed(() => `grid gap-[0.7rem] ${detailFrameClass.value}`)
const textSectionClass = computed(() => `grid gap-[0.55rem] ${detailFrameClass.value}`)

function formatBytes(size: number | null | undefined) {
  if (typeof size !== 'number' || !Number.isFinite(size) || size <= 0) {
    return ''
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div
    v-if="isShellActivity(display.activity)"
    :class="shellSectionClass"
  >
    <HighlightedCodeBlock
      v-if="shellCommand(display.activity)"
      :content="shellCommand(display.activity)"
      label="Command"
      language="bash"
      max-height="compact"
      copy-button-class="activity-command-copy"
      :copy-aria-label="`Copy command ${shellCommand(display.activity)}`"
    />

    <HighlightedCodeBlock
      v-if="hasShellTranscript(display.activity)"
      :content="shellOutputTranscript(display.activity)"
      label="Output"
      :meta-label="shellRuntime(display.activity)"
      auto-detect
      :copy-aria-label="`Copy output from ${shellCommand(display.activity) || display.activity.title}`"
    />

    <HighlightedCodeBlock
      v-if="!hasShellTranscript(display.activity) && display.activity.stdout"
      :content="display.activity.stdout"
      label="Stdout"
      :meta-label="shellRuntime(display.activity)"
      auto-detect
      :copy-aria-label="`Copy stdout from ${shellCommand(display.activity) || display.activity.title}`"
    />

    <HighlightedCodeBlock
      v-if="!hasShellTranscript(display.activity) && display.activity.stderr"
      :content="display.activity.stderr"
      label="Stderr"
      tone="danger"
      auto-detect
      :copy-aria-label="`Copy stderr from ${shellCommand(display.activity) || display.activity.title}`"
    />

    <p
      v-for="note in display.activity.meta"
      :key="`${display.activity.id}-${note}`"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      {{ note }}
    </p>
  </div>

  <div
    v-if="activityUsesMarkdown(display.activity) || isApprovalActivity(display.activity)"
    :class="textSectionClass"
  >
    <div
      v-if="activityUsesMarkdown(display.activity) && display.activity.detail"
      class="markdown-prose"
      v-html="renderMarkdown(display.activity.detail)"
      @click="onMarkdownClick"
    ></div>

    <TimelineApprovalSection
      v-if="isApprovalActivity(display.activity)"
      :activity="display.activity"
      @submit-approval="emit('submitApproval', $event)"
    />
  </div>

  <div
    v-if="display.activity.media?.kind === 'image'"
    :class="textSectionClass"
  >
    <a
      v-if="display.activity.media.url"
      :href="display.activity.media.url"
      target="_blank"
      rel="noreferrer"
      class="overflow-hidden rounded-2xl border border-[rgba(34,66,72,0.08)] bg-white/70 no-underline"
    >
      <img
        :src="display.activity.media.url"
        :alt="display.activity.media.label || display.activity.title"
        class="max-h-[22rem] w-full object-contain bg-[rgba(21,94,99,0.04)]"
      />
    </a>
    <div class="flex flex-wrap items-center gap-2 text-[0.8rem] text-[color:var(--app-text-soft)]">
      <span v-if="display.activity.media.label">{{ display.activity.media.label }}</span>
      <span v-if="display.activity.media.path">{{ display.activity.media.path }}</span>
      <span v-if="display.activity.media.mime_type">{{ display.activity.media.mime_type }}</span>
      <span v-if="formatBytes(display.activity.media.size)">{{ formatBytes(display.activity.media.size) }}</span>
    </div>
  </div>

  <div
    v-if="display.change && changeSummary"
    :class="shellSectionClass"
  >
    <div class="min-w-0">
      <div class="flex flex-wrap items-center gap-2">
        <p
          class="m-0 text-[0.78rem] font-bold uppercase tracking-[0.08em]"
          :class="fileChangeVerbClass(display.change)"
        >
          {{ fileChangeKindLabel(display.change) }}
        </p>
        <span
          v-if="changeSummary.additions > 0"
          class="inline-flex items-center rounded-full bg-[rgba(75,139,88,0.14)] px-2 py-0.5 text-[0.74rem] font-semibold text-[#4b8b58]"
        >
          +{{ changeSummary.additions }}
        </span>
        <span
          v-if="changeSummary.removals > 0"
          class="inline-flex items-center rounded-full bg-[rgba(184,93,72,0.14)] px-2 py-0.5 text-[0.74rem] font-semibold text-[#b85d48]"
        >
          -{{ changeSummary.removals }}
        </span>
        <span
          v-if="fileChangeMetaLabel(display.change)"
          class="inline-flex max-w-full items-center rounded-full border border-[rgba(34,66,72,0.1)] bg-[rgba(21,94,99,0.08)] px-2.5 py-1 text-[0.74rem] font-medium text-[color:var(--app-accent-deep)]"
        >
          <span class="truncate">
            {{ fileChangeMetaLabel(display.change) }}
          </span>
        </span>
      </div>
      <p class="mt-1 mb-0 break-all font-mono text-[0.84rem] text-[color:var(--app-text)]">
        {{ display.change.path }}
      </p>
    </div>

    <HighlightedCodeBlock
      v-if="display.change.diff"
      :content="display.change.diff"
      label="Diff"
      language="diff"
      :copy-aria-label="`Copy diff for ${display.change.path}`"
    />
  </div>

  <div
    v-if="
      !isShellActivity(display.activity) &&
      !display.change &&
      (Boolean(genericActivityDetail(display.activity)) ||
        Boolean(display.activity.command) ||
        Boolean(display.activity.cwd) ||
        Boolean(display.activity.stdout) ||
        Boolean(display.activity.stderr) ||
        display.activity.meta.length > 0)
    "
    :class="textSectionClass"
  >
    <p
      v-if="genericActivityDetail(display.activity)"
      class="m-0 break-words whitespace-pre-wrap text-[0.9rem] text-[color:var(--app-text)]"
    >
      {{ genericActivityDetail(display.activity) }}
    </p>
    <p
      v-if="display.activity.command"
      class="m-0 break-words whitespace-pre-wrap font-mono text-[0.9rem] text-[color:var(--app-accent-deep)]"
    >
      {{ display.activity.command }}
    </p>
    <p
      v-if="display.activity.cwd"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      cwd {{ display.activity.cwd }}
    </p>
    <p
      v-for="note in display.activity.meta"
      :key="`${display.activity.id}-${note}`"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      {{ note }}
    </p>

    <HighlightedCodeBlock
      v-if="display.activity.stdout"
      :content="display.activity.stdout"
      label="Stdout"
      auto-detect
      :copy-aria-label="`Copy stdout from ${activitySummaryText(display, isSending)}`"
    />

    <HighlightedCodeBlock
      v-if="display.activity.stderr"
      :content="display.activity.stderr"
      label="Stderr"
      tone="danger"
      auto-detect
      :copy-aria-label="`Copy stderr from ${activitySummaryText(display, isSending)}`"
    />
  </div>
</template>

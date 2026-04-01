<script setup lang="ts">
import { computed, ref } from 'vue'

import hljs from 'highlight.js/lib/common'
import { normalizeHighlightLanguage, resolveHighlightLanguage } from '../lib/codeHighlight'

const AUTO_DETECT_LIMIT = 12000

const props = withDefaults(
  defineProps<{
    content: string
    label: string
    language?: string
    autoDetect?: boolean
    metaLabel?: string
    tone?: 'default' | 'danger'
    maxHeight?: 'default' | 'compact'
    copyAriaLabel?: string
    copyButtonClass?: string
  }>(),
  {
    language: '',
    autoDetect: false,
    metaLabel: '',
    tone: 'default',
    maxHeight: 'default',
    copyAriaLabel: 'Copy code block',
    copyButtonClass: '',
  },
)

const copied = ref(false)
let copiedResetTimer: number | null = null

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function parseDiffHunkHeader(line: string) {
  const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
  if (!match) {
    return null
  }
  return {
    oldLine: Number.parseInt(match[1] ?? '0', 10),
    newLine: Number.parseInt(match[2] ?? '0', 10),
  }
}

function renderDiffContent(content: string) {
  let oldLineNumber: number | null = null
  let newLineNumber: number | null = null

  return content.split('\n').map((line) => {
    let marker = ' '
    let body = line
    let lineClass = 'diff-code-line'
    let oldLineLabel = ''
    let newLineLabel = ''

    if (line.startsWith('@@')) {
      lineClass += ' diff-code-line-hunk'
      marker = '@'
      const hunkHeader = parseDiffHunkHeader(line)
      oldLineNumber = hunkHeader?.oldLine ?? null
      newLineNumber = hunkHeader?.newLine ?? null
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      lineClass += ' diff-code-line-added'
      marker = '+'
      body = line.slice(1)
      newLineLabel = newLineNumber === null ? '' : String(newLineNumber)
      if (newLineNumber !== null) {
        newLineNumber += 1
      }
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      lineClass += ' diff-code-line-removed'
      marker = '-'
      body = line.slice(1)
      oldLineLabel = oldLineNumber === null ? '' : String(oldLineNumber)
      if (oldLineNumber !== null) {
        oldLineNumber += 1
      }
    } else if (
      line.startsWith('diff ') ||
      line.startsWith('index ') ||
      line.startsWith('+++') ||
      line.startsWith('---')
    ) {
      lineClass += ' diff-code-line-meta'
      marker = '·'
    } else {
      lineClass += ' diff-code-line-context'
      oldLineLabel = oldLineNumber === null ? '' : String(oldLineNumber)
      newLineLabel = newLineNumber === null ? '' : String(newLineNumber)
      if (oldLineNumber !== null) {
        oldLineNumber += 1
      }
      if (newLineNumber !== null) {
        newLineNumber += 1
      }
    }

    const escapedMarker = escapeHtml(marker)
    const escapedBody = escapeHtml(body) || ' '
    const escapedOldLine = escapeHtml(oldLineLabel)
    const escapedNewLine = escapeHtml(newLineLabel)
    return `<span class="${lineClass}"><span class="diff-code-line-number diff-code-line-number-old">${escapedOldLine}</span><span class="diff-code-line-number diff-code-line-number-new">${escapedNewLine}</span><span class="diff-code-gutter">${escapedMarker}</span><span class="diff-code-content">${escapedBody}</span></span>`
  }).join('')
}

function highlightContent(content: string, language: string, autoDetect: boolean) {
  const { requestedLanguage, highlightLanguage } = resolveHighlightLanguage(language)

  if (highlightLanguage === 'diff') {
    return {
      classNames: ['hljs', 'language-diff', 'diff-code'],
      content: renderDiffContent(content),
      detectedLanguage: requestedLanguage || highlightLanguage,
      isDiff: true,
    }
  }

  if (highlightLanguage && hljs.getLanguage(highlightLanguage)) {
    return {
      classNames: ['hljs', `language-${requestedLanguage || highlightLanguage}`],
      content: hljs.highlight(content, { language: highlightLanguage, ignoreIllegals: true }).value,
      detectedLanguage: requestedLanguage || highlightLanguage,
      isDiff: false,
    }
  }

  if (autoDetect && content.trim() && content.length <= AUTO_DETECT_LIMIT) {
    const autoDetected = hljs.highlightAuto(content)
    const detectedLanguage = normalizeHighlightLanguage(autoDetected.language ?? '')
    return {
      classNames: detectedLanguage ? ['hljs', `language-${detectedLanguage}`] : ['hljs'],
      content: autoDetected.value,
      detectedLanguage,
      isDiff: false,
    }
  }

  return {
    classNames: requestedLanguage ? ['hljs', `language-${requestedLanguage}`] : ['hljs'],
    content: escapeHtml(content),
    detectedLanguage: requestedLanguage,
    isDiff: false,
  }
}

const highlighted = computed(() =>
  highlightContent(props.content, props.language, props.autoDetect),
)

const resolvedLabel = computed(() => {
  const detectedLanguage = highlighted.value.detectedLanguage
  if (!props.autoDetect || !detectedLanguage) {
    return props.label
  }
  return `${props.label} · ${detectedLanguage}`
})

async function copyContent() {
  if (!props.content.trim()) {
    return
  }

  await navigator.clipboard.writeText(props.content)
  copied.value = true

  if (copiedResetTimer !== null) {
    window.clearTimeout(copiedResetTimer)
  }

  copiedResetTimer = window.setTimeout(() => {
    copied.value = false
    copiedResetTimer = null
  }, 1600)
}
</script>

<template>
  <div class="code-surface" :class="tone === 'danger' ? 'code-surface-danger' : ''">
    <div class="code-surface-toolbar">
      <div class="code-surface-toolbar-meta">
        <span class="code-surface-label">{{ resolvedLabel }}</span>
        <span v-if="metaLabel" class="code-surface-runtime">{{ metaLabel }}</span>
      </div>
      <button
        type="button"
        class="code-surface-copy"
        :class="copyButtonClass"
        :data-state="copied ? 'copied' : undefined"
        :aria-label="copied ? 'Copied' : copyAriaLabel"
        :title="copied ? 'Copied' : copyAriaLabel"
        @click="copyContent"
      >
        <i :class="copied ? 'pi pi-check' : 'pi pi-copy'" aria-hidden="true"></i>
      </button>
    </div>
    <div
      class="code-surface-scroll"
      :class="[
        maxHeight === 'compact' ? 'code-surface-scroll-compact' : '',
        highlighted.isDiff ? 'code-surface-scroll-diff' : '',
      ]"
    >
      <pre><code :class="highlighted.classNames" v-html="highlighted.content"></code></pre>
    </div>
  </div>
</template>

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
  }>(),
  {
    language: '',
    autoDetect: false,
    metaLabel: '',
    tone: 'default',
    maxHeight: 'default',
    copyAriaLabel: 'Copy code block',
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

function highlightContent(content: string, language: string, autoDetect: boolean) {
  const { requestedLanguage, highlightLanguage } = resolveHighlightLanguage(language)

  if (highlightLanguage && hljs.getLanguage(highlightLanguage)) {
    return {
      classNames: ['hljs', `language-${requestedLanguage || highlightLanguage}`],
      content: hljs.highlight(content, { language: highlightLanguage, ignoreIllegals: true }).value,
      detectedLanguage: requestedLanguage || highlightLanguage,
    }
  }

  if (autoDetect && content.trim() && content.length <= AUTO_DETECT_LIMIT) {
    const autoDetected = hljs.highlightAuto(content)
    const detectedLanguage = normalizeHighlightLanguage(autoDetected.language ?? '')
    return {
      classNames: detectedLanguage ? ['hljs', `language-${detectedLanguage}`] : ['hljs'],
      content: autoDetected.value,
      detectedLanguage,
    }
  }

  return {
    classNames: requestedLanguage ? ['hljs', `language-${requestedLanguage}`] : ['hljs'],
    content: escapeHtml(content),
    detectedLanguage: requestedLanguage,
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
      :class="maxHeight === 'compact' ? 'code-surface-scroll-compact' : ''"
    >
      <pre><code :class="highlighted.classNames" v-html="highlighted.content"></code></pre>
    </div>
  </div>
</template>

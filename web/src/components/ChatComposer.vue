<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

import Button from 'primevue/button'
import Textarea from 'primevue/textarea'

const model = defineModel<string>({ required: true })

const props = defineProps<{
  disabled: boolean
  isSending: boolean
  placeholder?: string
  selectionStart?: number
  selectionEnd?: number
  selectionVersion?: number
}>()

const emit = defineEmits<{
  submit: []
  selectionChange: [payload: { start: number; end: number }]
}>()

const textareaRef = ref<unknown>(null)
const composerMinRows = 1
const composerMaxRows = 12
const composerTextareaPt = {
  root: {
    style: {
      border: '0',
      background: 'transparent',
      boxShadow: 'none',
      padding: '0',
      resize: 'none',
      overflowX: 'hidden',
      lineHeight: '1.6',
    },
  },
}

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    emit('submit')
  }
}

function resolveNativeTextarea() {
  const instance = textareaRef.value as
    | HTMLTextAreaElement
    | { $el?: Element | null }
    | null

  if (instance instanceof HTMLTextAreaElement) {
    return instance
  }

  const root = instance && typeof instance === 'object' && '$el' in instance ? instance.$el : null
  if (root instanceof HTMLTextAreaElement) {
    return root
  }
  if (root instanceof Element) {
    const textarea = root.querySelector('textarea')
    return textarea instanceof HTMLTextAreaElement ? textarea : null
  }
  return null
}

function emitSelectionChange() {
  const textarea = resolveNativeTextarea()
  if (!textarea) {
    return
  }
  emit('selectionChange', {
    start: textarea.selectionStart ?? 0,
    end: textarea.selectionEnd ?? 0,
  })
}

function onInput() {
  emitSelectionChange()
  resizeComposerTextarea()
}

function resizeComposerTextarea() {
  const textarea = resolveNativeTextarea()
  if (!textarea) {
    return
  }

  const computedStyle = window.getComputedStyle(textarea)
  const fontSize = Number.parseFloat(computedStyle.fontSize) || 16
  const lineHeight = Number.parseFloat(computedStyle.lineHeight) || fontSize * 1.5
  const paddingTop = Number.parseFloat(computedStyle.paddingTop) || 0
  const paddingBottom = Number.parseFloat(computedStyle.paddingBottom) || 0
  const minHeight = lineHeight * composerMinRows + paddingTop + paddingBottom
  const maxHeight = lineHeight * composerMaxRows + paddingTop + paddingBottom

  textarea.style.height = 'auto'
  const nextHeight = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight)
  textarea.style.height = `${nextHeight}px`
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

watch(
  () => props.selectionVersion,
  async () => {
    await nextTick()
    const textarea = resolveNativeTextarea()
    if (!textarea) {
      return
    }
    const start = Math.max(0, Math.min(props.selectionStart ?? 0, model.value.length))
    const end = Math.max(start, Math.min(props.selectionEnd ?? 0, model.value.length))
    textarea.setSelectionRange(start, end)
    emitSelectionChange()
  },
)

watch(
  model,
  async () => {
    await nextTick()
    resizeComposerTextarea()
  },
  { flush: 'post' },
)

watch(
  () => props.disabled,
  async () => {
    await nextTick()
    resizeComposerTextarea()
  },
)

onMounted(async () => {
  await nextTick()
  resizeComposerTextarea()
})
</script>

<template>
  <div class="sticky bottom-0 shrink-0">
    <div
      class="rounded-[1.4rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px] max-sm:rounded-[1.15rem] max-sm:p-3"
    >
      <Textarea
        ref="textareaRef"
        v-model="model"
        class="composer-textarea"
        fluid
        rows="1"
        :pt="composerTextareaPt"
        :placeholder="placeholder ?? 'Ask yier to inspect code, read files, or operate inside the allowed roots…'"
        :disabled="disabled"
        @keydown="onKeydown"
        @input="onInput"
        @select="emitSelectionChange"
        @click="emitSelectionChange"
        @keyup="emitSelectionChange"
      />
      <div class="mt-3 flex items-center justify-between gap-3 max-sm:flex-col max-sm:items-stretch">
        <p class="m-0 text-sm text-[color:var(--app-text-soft)] max-sm:text-center">
          Press <span class="font-bold text-[color:var(--app-accent-deep)]">Ctrl/Cmd + Enter</span>
          to send.
        </p>
        <Button
          label="Send"
          icon="pi pi-arrow-up"
          class="max-sm:w-full"
          :disabled="disabled || !model.trim()"
          :loading="isSending"
          @click="emit('submit')"
        />
      </div>
    </div>
  </div>
</template>

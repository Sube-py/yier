<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

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
</script>

<template>
  <div class="composer-shell">
    <div class="composer-panel">
      <Textarea
        ref="textareaRef"
        v-model="model"
        auto-resize
        fluid
        rows="3"
        :placeholder="placeholder ?? 'Ask yier to inspect code, read files, or operate inside the allowed roots…'"
        :disabled="disabled"
        @keydown="onKeydown"
        @input="emitSelectionChange"
        @select="emitSelectionChange"
        @click="emitSelectionChange"
        @keyup="emitSelectionChange"
      />
      <div class="composer-actions">
        <p class="composer-hint">Press <span>Ctrl/Cmd + Enter</span> to send.</p>
        <Button
          label="Send"
          icon="pi pi-arrow-up"
          :disabled="disabled || !model.trim()"
          :loading="isSending"
          @click="emit('submit')"
        />
      </div>
    </div>
  </div>
</template>

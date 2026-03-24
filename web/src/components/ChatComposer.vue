<script setup lang="ts">
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'

const model = defineModel<string>({ required: true })

defineProps<{
  disabled: boolean
  isSending: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  submit: []
}>()

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    emit('submit')
  }
}
</script>

<template>
  <div class="composer-shell">
    <div class="composer-panel">
      <Textarea
        v-model="model"
        auto-resize
        fluid
        rows="3"
        :placeholder="placeholder ?? 'Ask yier to inspect code, read files, or operate inside the allowed roots…'"
        :disabled="disabled"
        @keydown="onKeydown"
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

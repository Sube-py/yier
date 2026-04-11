<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import Button from 'primevue/button'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'

import type { ComposerAttachmentState } from '../types/api'

const model = defineModel<string>({ required: true })

const props = defineProps<{
  disabled: boolean
  isSending: boolean
  attachments?: ComposerAttachmentState[]
  attachmentsEnabled?: boolean
  modelLabel?: string
  reasoningLabel?: string
  sandbox?: 'read-only' | 'workspace-write' | 'danger-full-access' | null
  savingSandbox?: boolean
  placeholder?: string
  selectionStart?: number
  selectionEnd?: number
  selectionVersion?: number
}>()

const emit = defineEmits<{
  submit: []
  saveSandbox: []
  uploadFiles: [files: File[]]
  removeAttachment: [localId: string]
  retryAttachment: [localId: string]
  selectionChange: [payload: { start: number; end: number }]
  updateSandbox: [value: 'read-only' | 'workspace-write' | 'danger-full-access']
}>()

const textareaRef = ref<unknown>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragActive = ref(false)
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
const sandboxOptions = [
  { label: 'Read only', value: 'read-only' },
  { label: 'Workspace write', value: 'workspace-write' },
  { label: 'Danger full access', value: 'danger-full-access' },
]

const attachments = computed(() => props.attachments ?? [])
const hasReadyAttachment = computed(() =>
  attachments.value.some((attachment) => attachment.status === 'ready'),
)
const canSubmit = computed(
  () => !props.disabled && (model.value.trim().length > 0 || hasReadyAttachment.value),
)

const reasoningEffortLabel = computed(() => {
  const value = (props.reasoningLabel ?? '').trim()
  if (!value) {
    return 'Medium'
  }
  if (value === 'xhigh') {
    return 'XHigh'
  }
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
})

const modelDisplayLabel = computed(() => {
  const value = (props.modelLabel ?? '').trim()
  return value || 'Default'
})

const isDangerSandbox = computed(() => props.sandbox === 'danger-full-access')

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

function onSandboxChange(value: 'read-only' | 'workspace-write' | 'danger-full-access') {
  if (!value || value === props.sandbox) {
    return
  }
  emit('updateSandbox', value)
  emit('saveSandbox')
}

function openFilePicker() {
  if (!props.attachmentsEnabled || props.disabled) {
    return
  }
  fileInputRef.value?.click()
}

function onFileInputChange(event: Event) {
  const target = event.target
  if (!(target instanceof HTMLInputElement) || !target.files?.length) {
    return
  }
  emit('uploadFiles', Array.from(target.files))
  target.value = ''
}

function onPaste(event: ClipboardEvent) {
  if (!props.attachmentsEnabled || !event.clipboardData?.files.length) {
    return
  }
  emit('uploadFiles', Array.from(event.clipboardData.files))
}

function onDrop(event: DragEvent) {
  isDragActive.value = false
  if (!props.attachmentsEnabled || !event.dataTransfer?.files.length) {
    return
  }
  emit('uploadFiles', Array.from(event.dataTransfer.files))
}

function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
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
  <div class="shrink-0">
    <div
      class="rounded-[1.35rem] bg-[rgba(255,250,242,0.82)] px-4 py-3 shadow-[inset_0_0_0_1px_rgba(34,66,72,0.06)] transition max-sm:rounded-[1.1rem] max-sm:px-3 max-sm:py-2.5"
      :class="{ 'ring-2 ring-[rgba(21,94,99,0.22)]': isDragActive }"
      @dragenter.prevent="isDragActive = true"
      @dragover.prevent="isDragActive = true"
      @dragleave.prevent="isDragActive = false"
      @drop.prevent="onDrop"
      @paste="onPaste"
    >
      <div
        v-if="attachments.length"
        class="mb-3 flex flex-wrap gap-2"
      >
        <div
          v-for="attachment in attachments"
          :key="attachment.local_id"
          class="group flex max-w-full items-center gap-2 rounded-2xl border border-[rgba(34,66,72,0.1)] bg-white/55 px-2.5 py-2 text-[0.78rem] text-[color:var(--app-text)]"
          :class="{ 'border-[#bc5f38]/35 bg-[#fff3ec]': attachment.status === 'error' }"
        >
          <img
            v-if="attachment.kind === 'image' && attachment.preview_url"
            :src="attachment.preview_url"
            alt=""
            class="h-8 w-8 rounded-xl object-cover"
          />
          <span
            v-else
            class="grid h-8 w-8 place-items-center rounded-xl bg-[rgba(21,94,99,0.1)] text-[color:var(--app-text-soft)]"
          >
            <i class="pi pi-paperclip text-xs"></i>
          </span>
          <span class="min-w-0">
            <span class="block truncate font-semibold">{{ attachment.name }}</span>
            <span class="block text-[0.72rem] text-[color:var(--app-text-soft)]">
              {{
                attachment.status === 'uploading'
                  ? 'Uploading...'
                  : attachment.status === 'error'
                    ? attachment.error || 'Upload failed'
                    : `${attachment.kind} · ${formatBytes(attachment.size)}`
              }}
            </span>
          </span>
          <button
            v-if="attachment.status === 'error'"
            type="button"
            class="rounded-full border border-[rgba(188,95,56,0.2)] bg-white/45 px-2 py-1 text-[0.72rem] font-semibold text-[#9b4b2d] transition hover:bg-white/70"
            :aria-label="`Retry ${attachment.name}`"
            @click="emit('retryAttachment', attachment.local_id)"
          >
            Retry
          </button>
          <button
            type="button"
            class="rounded-full border-0 bg-transparent p-1 text-[color:var(--app-text-soft)] transition hover:bg-black/5 hover:text-[color:var(--app-text)]"
            :aria-label="`Remove ${attachment.name}`"
            @click="emit('removeAttachment', attachment.local_id)"
          >
            <i class="pi pi-times text-[0.68rem]"></i>
          </button>
        </div>
      </div>

      <Textarea
        ref="textareaRef"
        v-model="model"
        class="composer-textarea"
        fluid
        rows="1"
        :pt="composerTextareaPt"
        :placeholder="placeholder ?? 'Ask yier to inspect code, read files, or operate inside the allowed roots...'"
        :disabled="disabled"
        @keydown="onKeydown"
        @input="onInput"
        @select="emitSelectionChange"
        @click="emitSelectionChange"
        @keyup="emitSelectionChange"
      />
      <div class="mt-3 flex items-center justify-between gap-3">
        <div class="min-w-0 overflow-x-auto">
          <div class="flex min-w-max items-center gap-2">
            <button
              v-if="attachmentsEnabled"
              type="button"
              class="inline-flex items-center gap-1 rounded-full border-0 bg-transparent px-1 py-0.5 text-left text-[0.78rem] font-medium tracking-[0.01em] text-[color:var(--app-text-soft)] transition hover:bg-white/36"
              :disabled="disabled"
              aria-label="Attach files"
              @click="openFilePicker"
            >
              <i class="pi pi-paperclip text-[0.78rem]"></i>
              <span>Attach</span>
            </button>
            <input
              ref="fileInputRef"
              class="hidden"
              type="file"
              multiple
              @change="onFileInputChange"
            />
            <span
              v-if="attachmentsEnabled"
              class="h-3.5 w-px bg-[rgba(34,66,72,0.1)]"
            ></span>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-full border-0 bg-transparent px-1 py-0.5 text-left transition hover:bg-white/36"
              aria-label="Choose model"
            >
              <span class="truncate text-[0.78rem] font-medium tracking-[0.01em] text-[color:var(--app-text-soft)]">
                {{ modelDisplayLabel }}
              </span>
              <i class="pi pi-chevron-down text-[0.6rem] text-[color:var(--app-text-soft)]"></i>
            </button>
            <span class="h-3.5 w-px bg-[rgba(34,66,72,0.1)]"></span>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-full border-0 bg-transparent px-1 py-0.5 text-left transition hover:bg-white/36"
              aria-label="Choose reasoning effort"
            >
              <span class="truncate text-[0.78rem] font-medium tracking-[0.01em] text-[color:var(--app-text-soft)]">
                {{ reasoningEffortLabel }}
              </span>
              <i class="pi pi-chevron-down text-[0.6rem] text-[color:var(--app-text-soft)]"></i>
            </button>
            <template v-if="sandbox">
              <span class="h-3.5 w-px bg-[rgba(34,66,72,0.1)]"></span>
              <Select
                :model-value="sandbox"
                :options="sandboxOptions"
                option-label="label"
                option-value="value"
                size="small"
                class="composer-inline-select"
                :class="{ 'composer-inline-select-danger': isDangerSandbox }"
                :disabled="savingSandbox"
                aria-label="Choose permission mode"
                @update:model-value="onSandboxChange"
              />
            </template>
          </div>
        </div>
        <Button
          icon="pi pi-arrow-up"
          aria-label="Send message"
          class="shrink-0 sm:!h-11 sm:!w-11 sm:!px-0"
          :disabled="!canSubmit"
          :loading="isSending"
          @click="emit('submit')"
        />
      </div>
    </div>
  </div>
</template>

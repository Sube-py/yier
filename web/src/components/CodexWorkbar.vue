<script setup lang="ts">
import { computed } from 'vue'

import Button from 'primevue/button'
import Tag from 'primevue/tag'

import type { BackendRuntime, CodexPairingExtensionSummary } from '../types/api'

const props = withDefaults(
  defineProps<{
    runtime: BackendRuntime | null
    projectPath: string
    pairedEditors: CodexPairingExtensionSummary[]
    surface?: 'sidebar' | 'sheet'
    closeable?: boolean
  }>(),
  {
    surface: 'sidebar',
    closeable: false,
  },
)

const emit = defineEmits<{
  close: []
}>()

const isSheet = computed(() => props.surface === 'sheet')

function runtimeStatusLabel(status: string | null | undefined) {
  const normalized = (status ?? '').trim()
  if (!normalized || normalized === 'idle') {
    return 'Ready'
  }
  if (normalized === 'active') {
    return 'Working'
  }
  if (normalized === 'completed') {
    return 'Completed'
  }
  if (normalized === 'interrupted') {
    return 'Aborted'
  }
  if (normalized === 'error' || normalized === 'failed') {
    return 'Error'
  }
  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function runtimeStatusSeverity(status: string | null | undefined) {
  const normalized = (status ?? '').trim()
  if (!normalized || normalized === 'idle' || normalized === 'completed') {
    return 'success'
  }
  if (normalized === 'active') {
    return 'info'
  }
  if (normalized === 'interrupted') {
    return 'warn'
  }
  if (normalized === 'error' || normalized === 'failed') {
    return 'danger'
  }
  return 'contrast'
}

function pairingCapabilitySummary(capabilityNames: string[]) {
  if (!capabilityNames.length) {
    return 'No linked actions'
  }
  return capabilityNames.join(', ')
}
</script>

<template>
  <aside class="flex min-h-0 flex-col gap-4">
    <div
      v-if="isSheet"
      class="flex flex-col gap-3 border-b border-[color:var(--app-border)] pb-3"
    >
      <div class="sheet-handle"></div>
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="eyebrow">Codex workspace</p>
          <h4>Runtime</h4>
        </div>
        <Button
          v-if="closeable"
          icon="pi pi-times"
          rounded
          text
          severity="secondary"
          aria-label="Close runtime panel"
          @click="emit('close')"
        />
      </div>
    </div>

    <section
      class="grid gap-4 rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="eyebrow">Codex runtime</p>
          <h4>{{ runtime?.label ?? 'Codex App Server' }}</h4>
        </div>
        <Tag
          :value="runtimeStatusLabel(runtime?.status)"
          :severity="runtimeStatusSeverity(runtime?.status)"
          rounded
        />
      </div>
      <p
        v-if="runtime?.detail"
        class="m-0 text-[0.85rem] leading-[1.55] text-[color:var(--app-text-soft)]"
      >
        {{ runtime.detail }}
      </p>

      <div class="grid gap-3">
        <div class="flex items-start justify-between gap-3 text-sm text-[color:var(--app-text-soft)]">
          <span>Thread</span>
          <code class="max-w-52 break-all text-xs text-[color:var(--app-accent-deep)]">
            {{ runtime?.thread_id ?? 'Pending' }}
          </code>
        </div>
        <div class="flex items-start justify-between gap-3 text-sm text-[color:var(--app-text-soft)]">
          <span>Pending approvals</span>
          <strong>{{ runtime?.pending_approval_count ?? 0 }}</strong>
        </div>
        <div class="grid gap-1 text-sm text-[color:var(--app-text-soft)]">
          <span>Project</span>
          <code class="max-w-52 break-all text-xs text-[color:var(--app-accent-deep)]">
            {{ projectPath || 'Not set' }}
          </code>
        </div>
        <div class="grid gap-1 text-sm text-[color:var(--app-text-soft)]">
          <span>Flags</span>
          <p class="m-0 text-sm text-[color:var(--app-text-soft)]">
            {{ runtime?.active_flags?.length ? runtime.active_flags.join(', ') : 'No active flags' }}
          </p>
        </div>
      </div>
    </section>

    <section
      class="grid gap-4 rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      :class="{ 'order-2': isSheet }"
    >
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="eyebrow">Desktop bridge</p>
          <h4>Paired editors</h4>
        </div>
        <Tag :value="`${pairedEditors.length} online`" severity="secondary" rounded />
      </div>

      <div v-if="pairedEditors.length" class="grid gap-3">
        <article
          v-for="editor in pairedEditors"
          :key="editor.id"
          class="grid gap-2 rounded-2xl border border-[rgba(136,109,67,0.18)] bg-[linear-gradient(180deg,rgba(255,251,245,0.9),rgba(247,238,224,0.72))] p-3 max-sm:p-2.5"
        >
          <div class="flex items-start justify-between gap-3 max-sm:flex-col max-sm:items-stretch">
            <div>
              <p class="m-0 font-semibold text-[color:var(--app-text)]">
                {{ editor.workspace_name || editor.app_name }}
              </p>
              <p class="m-0 text-[0.85rem] leading-[1.55] text-[color:var(--app-text-soft)]">
                {{ editor.app_name }}
                <span v-if="editor.extension_version"> · {{ editor.extension_version }}</span>
              </p>
            </div>
            <Tag
              :value="editor.needs_reload ? 'Reload needed' : 'Ready'"
              :severity="editor.needs_reload ? 'warn' : 'success'"
              rounded
            />
          </div>
          <p class="m-0 text-[0.85rem] leading-[1.55] text-[color:var(--app-text-soft)]">
            {{ editor.capability_count }} linked actions ·
            {{ pairingCapabilitySummary(editor.capability_names) }}
          </p>
          <code class="break-all text-xs text-[color:var(--app-accent-deep)]">
            {{ editor.socket_path }}
          </code>
        </article>
      </div>
      <p v-else class="m-0 text-[0.85rem] leading-[1.55] text-[color:var(--app-text-soft)]">
        No active editor pairings detected from
        <code class="break-all text-xs text-[color:var(--app-accent-deep)]">
          app_pairing_extensions
        </code>.
      </p>
    </section>
  </aside>
</template>

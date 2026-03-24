<script setup lang="ts">
import Button from 'primevue/button'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

import type { BackendRuntime, CodexPairingExtensionSummary } from '../types/api'

defineProps<{
  runtime: BackendRuntime | null
  projectPath: string
  pairedEditors: CodexPairingExtensionSummary[]
  sandbox: 'read-only' | 'workspace-write' | 'danger-full-access'
  saving: boolean
}>()

const emit = defineEmits<{
  updateSandbox: [value: 'read-only' | 'workspace-write' | 'danger-full-access']
  saveSandbox: []
}>()

const sandboxOptions = [
  { label: 'Read only', value: 'read-only' },
  { label: 'Workspace write', value: 'workspace-write' },
  { label: 'Danger full access', value: 'danger-full-access' },
]

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
  <aside class="codex-workbar">
    <section class="runtime-card codex-runtime-card">
      <div class="codex-workbar-header">
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
      <p v-if="runtime?.detail" class="codex-pairing-copy">
        {{ runtime.detail }}
      </p>

      <div class="codex-runtime-grid">
        <div class="codex-runtime-row">
          <span>Thread</span>
          <code>{{ runtime?.thread_id ?? 'Pending' }}</code>
        </div>
        <div class="codex-runtime-row">
          <span>Pending approvals</span>
          <strong>{{ runtime?.pending_approval_count ?? 0 }}</strong>
        </div>
        <div class="codex-runtime-row codex-runtime-row--stacked">
          <span>Project</span>
          <code>{{ projectPath || 'Not set' }}</code>
        </div>
        <div class="codex-runtime-row codex-runtime-row--stacked">
          <span>Flags</span>
          <p class="codex-runtime-flags">
            {{ runtime?.active_flags?.length ? runtime.active_flags.join(', ') : 'No active flags' }}
          </p>
        </div>
      </div>
    </section>

    <section class="runtime-card codex-runtime-card">
      <div class="codex-workbar-header">
        <div>
          <p class="eyebrow">Desktop bridge</p>
          <h4>Paired editors</h4>
        </div>
        <Tag :value="`${pairedEditors.length} online`" severity="secondary" rounded />
      </div>

      <div v-if="pairedEditors.length" class="codex-pairing-list">
        <article
          v-for="editor in pairedEditors"
          :key="editor.id"
          class="codex-pairing-item"
        >
          <div class="codex-pairing-topline">
            <div>
              <p class="codex-pairing-title">
                {{ editor.workspace_name || editor.app_name }}
              </p>
              <p class="codex-pairing-meta">
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
          <p class="codex-pairing-copy">
            {{ editor.capability_count }} linked actions ·
            {{ pairingCapabilitySummary(editor.capability_names) }}
          </p>
          <code>{{ editor.socket_path }}</code>
        </article>
      </div>
      <p v-else class="codex-pairing-empty">
        No active editor pairings detected from <code>app_pairing_extensions</code>.
      </p>
    </section>

    <section class="runtime-card codex-runtime-card">
      <div class="codex-workbar-header">
        <div>
          <p class="eyebrow">Permission mode</p>
          <h4>Global sandbox default</h4>
        </div>
      </div>

      <div class="codex-permission-panel">
        <Select
          :model-value="sandbox"
          :options="sandboxOptions"
          option-label="label"
          option-value="value"
          fluid
          @update:model-value="emit('updateSandbox', $event)"
        />
        <p class="codex-permission-copy">
          This updates the default Codex sandbox for all sessions and applies on the next turn.
        </p>
        <Button
          label="Save Permission Mode"
          icon="pi pi-shield"
          :loading="saving"
          @click="emit('saveSandbox')"
        />
      </div>
    </section>
  </aside>
</template>

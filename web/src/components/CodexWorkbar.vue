<script setup lang="ts">
import Button from 'primevue/button'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

import type { BackendRuntime } from '../types/api'

defineProps<{
  runtime: BackendRuntime | null
  projectPath: string
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
</script>

<template>
  <aside class="codex-workbar">
    <section class="runtime-card codex-runtime-card">
      <div class="codex-workbar-header">
        <div>
          <p class="eyebrow">Codex runtime</p>
          <h4>{{ runtime?.label ?? 'Codex App Server' }}</h4>
        </div>
        <Tag :value="runtime?.status ?? 'idle'" severity="contrast" rounded />
      </div>

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

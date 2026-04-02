<script setup lang="ts">
import Button from 'primevue/button'
import Select from 'primevue/select'

import CodexSessionExplorer from '../CodexSessionExplorer.vue'
import WorkspaceBrandPanel from './WorkspaceBrandPanel.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet-fade">
      <div
        v-if="workspace.showSidebarDrawer"
        class="sheet-overlay"
        @click="workspace.closeSidebarDrawer"
      ></div>
    </Transition>
    <Transition name="drawer-slide">
      <aside
        v-if="workspace.showSidebarDrawer"
        class="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Codex sidebar"
      >
        <div class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] px-4 py-4">
          <div>
            <p class="eyebrow">Codex workspace</p>
            <h4>Sidebar</h4>
          </div>
          <Button
            icon="pi pi-times"
            rounded
            text
            severity="secondary"
            aria-label="Close sidebar"
            @click="workspace.closeSidebarDrawer"
          />
        </div>

        <div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
          <WorkspaceBrandPanel variant="codex" />

          <Button
            label="New thread"
            icon="pi pi-pen-to-square"
            fluid
            :pt="{
              root: {
                class:
                  'justify-center gap-2.5 border border-transparent bg-white/52 px-[0.82rem] py-[0.72rem] text-[color:var(--app-text)] shadow-none',
              },
              label: {
                class: 'text-center font-semibold',
              },
              icon: {
                class: 'm-0',
              },
            }"
            @click="workspace.handleCodexSessionStart(workspace.activeProjectPath)"
          />

          <CodexSessionExplorer
            :projects="workspace.activeCodexProjects"
            :active-session-id="workspace.activeSessionId"
            :active-session-status="workspace.activeSessionRuntime?.status ?? null"
            :active-project-path="workspace.activeProjectPath"
            @open-session="workspace.openCodexNativeSession"
            @start-session="workspace.handleCodexSessionStart"
          />

          <div
            class="grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
          >
            <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Workspaces</p>
            <Select
              v-model="workspace.workspaceSurfaceModel"
              :options="workspace.workspaceSurfaceOptions"
              option-label="label"
              option-value="value"
              option-disabled="disabled"
              class="workspace-switcher-control w-full"
              size="small"
              aria-label="Switch workspace"
            />
          </div>
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

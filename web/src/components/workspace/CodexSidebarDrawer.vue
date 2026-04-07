<script setup lang="ts">
import Button from 'primevue/button'

import WorkspaceSidebarContent from './WorkspaceSidebarContent.vue'
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
        :aria-label="`${workspace.assistantLabel} workspace sidebar`"
      >
        <div class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] px-4 py-4">
          <div>
            <p class="eyebrow">{{ workspace.assistantLabel }} workspace</p>
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
          <WorkspaceSidebarContent surface="drawer" />
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

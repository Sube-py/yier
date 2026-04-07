<script setup lang="ts">
import WorkspaceSidebarContent from './WorkspaceSidebarContent.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <aside
    v-if="!workspace.isCodexCompactLayout"
    class="flex min-h-0 flex-col gap-4 overflow-hidden max-[1023px]:order-2 max-[1023px]:gap-3 max-[1023px]:overflow-visible"
  >
    <WorkspaceSidebarContent />
  </aside>
</template>

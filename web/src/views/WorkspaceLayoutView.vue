<script setup lang="ts">
import Message from 'primevue/message'
import { RouterView } from 'vue-router'

import CodexRuntimeSheet from '../components/workspace/CodexRuntimeSheet.vue'
import CodexSidebarDrawer from '../components/workspace/CodexSidebarDrawer.vue'
import WorkspaceHeader from '../components/workspace/WorkspaceHeader.vue'
import WorkspaceSidebar from '../components/workspace/WorkspaceSidebar.vue'
import { provideWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = provideWorkspaceAppContext()
</script>

<template>
  <div
    class="grid h-screen grid-cols-[minmax(17rem,20rem)_minmax(0,1fr)] gap-6 overflow-hidden p-6"
    :class="
      workspace.isMobileChatPage
        ? 'max-[1023px]:h-[100dvh] max-[1023px]:grid-cols-1 max-[1023px]:gap-0 max-[1023px]:overflow-hidden max-[1023px]:p-0'
        : 'max-[1023px]:h-auto max-[1023px]:grid-cols-1 max-[1023px]:gap-4 max-[1023px]:overflow-visible max-[1023px]:p-4 max-sm:gap-3 max-sm:p-3'
    "
  >
    <WorkspaceSidebar />

    <main
      class="flex min-h-0 flex-col gap-4 overflow-hidden rounded-[1.8rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.15rem] shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      :class="
        workspace.isMobileChatPage
          ? 'max-[1023px]:h-full max-[1023px]:min-h-0 max-[1023px]:gap-3 max-[1023px]:overflow-hidden max-[1023px]:rounded-none max-[1023px]:border-0 max-[1023px]:bg-transparent max-[1023px]:p-0 max-[1023px]:shadow-none max-[1023px]:backdrop-blur-none'
          : 'max-[1023px]:min-h-auto max-[1023px]:gap-3 max-[1023px]:overflow-visible max-[1023px]:rounded-[1.45rem] max-[1023px]:p-4 max-sm:p-3'
      "
    >
      <WorkspaceHeader />

      <Message v-if="workspace.errorMessage" severity="error" class="m-0">
        {{ workspace.errorMessage }}
      </Message>
      <Message v-else-if="workspace.successMessage" severity="success" class="m-0">
        {{ workspace.successMessage }}
      </Message>

      <RouterView />
    </main>
  </div>

  <CodexSidebarDrawer />
  <CodexRuntimeSheet />
</template>

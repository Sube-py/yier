<script setup lang="ts">
import CodexWorkbar from '../CodexWorkbar.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet-fade">
      <div
        v-if="workspace.showRuntimeSheet"
        class="sheet-overlay"
        @click="workspace.closeRuntimeSheet"
      ></div>
    </Transition>
    <Transition name="sheet-slide">
      <section
        v-if="workspace.showRuntimeSheet"
        class="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Codex runtime"
      >
        <CodexWorkbar
          surface="sheet"
          closeable
          :runtime="workspace.activeSessionRuntime"
          :project-path="workspace.activeProjectPath"
          :paired-editors="workspace.activeCodexPairedEditors"
          @close="workspace.closeRuntimeSheet"
        />
      </section>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, type CSSProperties } from 'vue'

import CodexAdvancedModePanel from './CodexAdvancedModePanel.vue'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()

const dragPointerId = ref<number | null>(null)
const dragStartX = ref(0)
const dragStartY = ref(0)
const dragStartTop = ref(0)
const dragMoved = ref(false)

const fabStyle = computed<CSSProperties>(() => ({
  top: `${workspace.codexAdvancedModeFabTop}px`,
  left: workspace.codexAdvancedModeFabEdge === 'left' ? '0.4rem' : undefined,
  right: workspace.codexAdvancedModeFabEdge === 'right' ? '0.4rem' : undefined,
}))

const statusDotClass = computed(() => {
  const status = workspace.activeCodexGoalLoop?.status ?? 'idle'
  if (status === 'running') {
    return 'bg-[#3e8b69] shadow-[0_0_0_4px_rgba(62,139,105,0.16)]'
  }
  if (status === 'blocked' || status === 'failed') {
    return 'bg-[#b85d48] shadow-[0_0_0_4px_rgba(184,93,72,0.14)]'
  }
  if (status === 'paused') {
    return 'bg-[#8f7034] shadow-[0_0_0_4px_rgba(143,112,52,0.14)]'
  }
  if (status === 'completed') {
    return 'bg-[#4b8b58] shadow-[0_0_0_4px_rgba(75,139,88,0.14)]'
  }
  return 'bg-[rgba(34,66,72,0.26)]'
})

function clampFabTop(value: number) {
  if (typeof window === 'undefined') {
    return value
  }
  return Math.min(Math.max(Math.round(value), 88), Math.max(88, window.innerHeight - 88))
}

function cleanupFabDragListeners() {
  window.removeEventListener('pointermove', handleFabPointerMove)
  window.removeEventListener('pointerup', handleFabPointerUp)
  window.removeEventListener('pointercancel', handleFabPointerUp)
}

function handleFabPointerDown(event: PointerEvent) {
  if (event.button !== 0) {
    return
  }
  dragPointerId.value = event.pointerId
  dragStartX.value = event.clientX
  dragStartY.value = event.clientY
  dragStartTop.value = workspace.codexAdvancedModeFabTop
  dragMoved.value = false
  cleanupFabDragListeners()
  window.addEventListener('pointermove', handleFabPointerMove)
  window.addEventListener('pointerup', handleFabPointerUp)
  window.addEventListener('pointercancel', handleFabPointerUp)
}

function handleFabPointerMove(event: PointerEvent) {
  if (dragPointerId.value !== event.pointerId) {
    return
  }
  const nextTop = clampFabTop(dragStartTop.value + event.clientY - dragStartY.value)
  const nextEdge = event.clientX < window.innerWidth / 2 ? 'left' : 'right'
  if (
    Math.abs(event.clientX - dragStartX.value) > 6 ||
    Math.abs(event.clientY - dragStartY.value) > 6
  ) {
    dragMoved.value = true
  }
  workspace.setCodexAdvancedModeFabPosition(nextEdge, nextTop, { persist: false })
}

function handleFabPointerUp(event: PointerEvent) {
  if (dragPointerId.value !== event.pointerId) {
    return
  }
  dragPointerId.value = null
  cleanupFabDragListeners()
  workspace.setCodexAdvancedModeFabPosition(
    workspace.codexAdvancedModeFabEdge,
    workspace.codexAdvancedModeFabTop,
  )
}

function handleFabClick(event: MouseEvent) {
  if (dragMoved.value) {
    dragMoved.value = false
    event.preventDefault()
    event.stopPropagation()
    return
  }
  workspace.toggleCodexAdvancedMode()
}

onBeforeUnmount(() => {
  cleanupFabDragListeners()
})
</script>

<template>
  <Teleport to="body">
    <button
      v-if="workspace.showCodexAdvancedModeFab"
      type="button"
      data-testid="advanced-mode-fab"
      class="advanced-mode-fab"
      :class="[
        workspace.codexAdvancedModeFabEdge === 'left'
          ? 'advanced-mode-fab--left'
          : 'advanced-mode-fab--right',
        workspace.isCodexGoalLoopRunning ? 'advanced-mode-fab--running' : '',
        workspace.isCodexAdvancedModeOpen ? 'advanced-mode-fab--open' : '',
      ]"
      :style="fabStyle"
      :data-running="workspace.isCodexGoalLoopRunning ? 'true' : 'false'"
      :aria-expanded="workspace.showCodexAdvancedModeOverlay"
      aria-label="Open advanced mode"
      @pointerdown="handleFabPointerDown"
      @click="handleFabClick"
    >
      <span class="advanced-mode-fab__icon">
        <i class="pi pi-sparkles text-[0.92rem]"></i>
      </span>
      <span
        class="advanced-mode-fab__dot"
        :class="[statusDotClass, workspace.isCodexGoalLoopRunning ? 'animate-pulse' : '']"
      ></span>
    </button>

    <Transition name="sheet-fade">
      <div
        v-if="workspace.showCodexAdvancedModeOverlay"
        class="sheet-overlay"
        @click="workspace.closeCodexAdvancedMode"
      ></div>
    </Transition>

    <Transition name="sheet-slide">
      <section
        v-if="workspace.showCodexAdvancedModeSheet"
        data-testid="advanced-mode-sheet"
        class="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Advanced mode"
      >
        <div class="sheet-handle mb-3"></div>
        <CodexAdvancedModePanel mobile closeable />
      </section>
    </Transition>

    <Transition name="sheet-fade">
      <section
        v-if="workspace.showCodexAdvancedModePanel"
        data-testid="advanced-mode-panel"
        class="advanced-mode-desktop-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Advanced mode"
      >
        <CodexAdvancedModePanel closeable />
      </section>
    </Transition>
  </Teleport>
</template>

import type { Ref } from 'vue'

import type { CodexWorkMode } from '../types/api'

interface PlanModeKeyboardOptions {
  isPlanMode: Ref<boolean>
  disabled: Ref<boolean>
  onToggle: () => void
}

/**
 * Handles Shift+Tab keyboard shortcut to toggle plan/build mode
 * when the composer textarea is focused.
 *
 * Codex uses CmdOrCtrl+Shift+P globally and Shift+Tab when composer is focused;
 * we implement only Shift+Tab to keep it simple and avoid conflicts.
 */
export function usePlanModeKeyboard(options: PlanModeKeyboardOptions) {
  function onComposerKeydown(event: KeyboardEvent) {
    if (options.disabled.value) return

    if (event.key === 'Tab' && event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault()
      event.stopPropagation()
      options.onToggle()
    }
  }

  return { onComposerKeydown }
}

/**
 * Toggle between plan and build mode.
 */
export function toggleWorkMode(current: CodexWorkMode): CodexWorkMode {
  return current === 'plan' ? 'build' : 'plan'
}

<script setup lang="ts">
import Button from 'primevue/button'

import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <header
    v-if="workspace.isMobileChatPage"
    class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 rounded-[1.15rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,252,245,0.72)] px-2.5 py-2 shadow-[0_14px_28px_rgba(31,54,58,0.08)] backdrop-blur-[14px]"
  >
    <button
      type="button"
      class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.95rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,255,255,0.72)] text-[color:var(--app-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.48)] transition hover:bg-white"
      aria-label="Open workspace menu"
      @click="workspace.openSidebarDrawer"
    >
      <i class="pi pi-bars text-[0.98rem]"></i>
    </button>
    <div class="min-w-0 text-center">
      <p class="m-0 text-[0.68rem] font-bold uppercase tracking-[0.18em] text-[color:var(--app-text-soft)]">
        Workspace
      </p>
      <div class="mt-1 inline-flex max-w-full items-center gap-2 rounded-full bg-[rgba(21,94,99,0.08)] px-3 py-1.25 text-[color:var(--app-accent-deep)] shadow-[inset_0_1px_0_rgba(255,255,255,0.38)]">
        <span class="h-2 w-2 shrink-0 rounded-full bg-[color:var(--app-accent)]"></span>
        <span class="truncate font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[1.05rem] font-semibold leading-none">
          {{ workspace.assistantLabel }}
        </span>
      </div>
    </div>
    <button
      type="button"
      class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.95rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,255,255,0.72)] text-[color:var(--app-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.48)] transition hover:bg-white"
      aria-label="Open settings"
      @click="workspace.openSettings"
    >
      <i class="pi pi-cog text-[0.98rem]"></i>
    </button>
  </header>

  <header
    v-else
    class="flex items-start justify-between gap-4 max-[1023px]:flex-col max-[1023px]:items-stretch"
  >
    <div>
      <p class="eyebrow">{{ workspace.workspaceEyebrow }}</p>
      <h2 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[clamp(1.6rem,2vw,2.4rem)] leading-[1.1] font-semibold">
        {{ workspace.workspaceTitle }}
      </h2>
    </div>
    <div class="flex items-center gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch">
      <div
        v-if="workspace.isCodexWorkspace"
        class="codex-toolbar-primary inline-flex rounded-full border border-[color:var(--app-border)] bg-[rgba(248,243,233,0.9)] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.6)]"
      >
        <button
          type="button"
          class="codex-mode-button min-w-[4.8rem] cursor-pointer rounded-full bg-transparent px-[0.9rem] py-[0.55rem] font-bold text-[color:var(--app-text-soft)] transition disabled:cursor-default disabled:opacity-65"
          :class="{
            'bg-[color:var(--app-accent)] text-[#f7f5ef]': workspace.activeCodexWorkMode === 'plan',
          }"
          :disabled="workspace.savingState.codexMode"
          @click="workspace.updateCodexWorkMode('plan')"
        >
          Plan
        </button>
        <button
          type="button"
          class="codex-mode-button min-w-[4.8rem] cursor-pointer rounded-full bg-transparent px-[0.9rem] py-[0.55rem] font-bold text-[color:var(--app-text-soft)] transition disabled:cursor-default disabled:opacity-65"
          :class="{
            'bg-[color:var(--app-accent)] text-[#f7f5ef]': workspace.activeCodexWorkMode === 'build',
          }"
          :disabled="workspace.savingState.codexMode"
          @click="workspace.updateCodexWorkMode('build')"
        >
          Build
        </button>
      </div>
      <Button
        :label="workspace.isChatRoute ? 'Settings' : 'Back to Chat'"
        :icon="workspace.isChatRoute ? 'pi pi-sliders-h' : 'pi pi-comments'"
        severity="secondary"
        text
        :aria-label="workspace.isChatRoute ? 'Open settings' : 'Back to chat'"
        @click="workspace.isChatRoute ? workspace.openSettings() : workspace.openChat()"
      />
    </div>
  </header>
</template>

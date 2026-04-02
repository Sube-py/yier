<script setup lang="ts">
import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

import CodexSessionExplorer from '../CodexSessionExplorer.vue'
import WorkspaceBrandPanel from './WorkspaceBrandPanel.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <aside
    v-if="!workspace.showCodexMobileChrome"
    class="flex min-h-0 flex-col gap-4 overflow-hidden max-[1023px]:order-2 max-[1023px]:gap-3 max-[1023px]:overflow-visible"
  >
    <WorkspaceBrandPanel :variant="workspace.isCodexWorkspace ? 'codex' : 'yier'" />

    <div
      v-if="!workspace.isBooting && !workspace.isCodexWorkspace"
      class="rail-actions grid gap-3"
    >
      <Button
        label="New Chat"
        icon="pi pi-plus"
        fluid
        @click="workspace.handleNewChatClick"
      />
    </div>

    <div
      v-if="!workspace.isBooting && workspace.isCodexWorkspace"
      class="rail-actions grid gap-3"
    >
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
    </div>

    <CodexSessionExplorer
      v-if="!workspace.isBooting && workspace.isCodexWorkspace && !workspace.isCodexCompactLayout"
      :projects="workspace.activeCodexProjects"
      :active-session-id="workspace.activeSessionId"
      :active-session-status="workspace.activeSessionRuntime?.status ?? null"
      :active-project-path="workspace.activeProjectPath"
      @open-session="workspace.openCodexNativeSession"
      @start-session="workspace.handleCodexSessionStart"
    />
    <div
      v-else-if="!workspace.isBooting"
      class="flex min-h-0 flex-1 flex-col gap-[0.85rem] overflow-hidden rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <div class="flex items-center justify-between gap-4">
        <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Recent sessions</p>
        <Tag
          :value="String(workspace.sidebarSessionHistoryCount)"
          severity="secondary"
          rounded
        />
      </div>

      <ScrollPanel
        v-if="workspace.sidebarSessionHistory.length"
        class="min-h-0 flex-1"
      >
        <div class="grid gap-[0.65rem] pr-[0.35rem]">
          <div
            v-for="session in workspace.sidebarSessionHistory"
            :key="session.session_id"
            class="session-history-item grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] p-1 transition duration-150 hover:border-[rgba(21,94,99,0.18)] hover:bg-[rgba(255,250,242,0.92)] hover:-translate-y-px"
            :class="{
              'session-history-item--active border-[rgba(21,94,99,0.28)] bg-[rgba(222,241,239,0.52)] shadow-[inset_0_0_0_1px_rgba(21,94,99,0.08)]':
                session.session_id === workspace.activeSessionId,
            }"
          >
            <button
              type="button"
              class="session-history-main rounded-[0.8rem] border-0 bg-transparent px-[0.65rem] py-[0.55rem] text-left text-inherit transition hover:bg-[rgba(21,94,99,0.06)] focus-visible:outline-2 focus-visible:outline-[rgba(21,94,99,0.38)]"
              @click="workspace.openSessionFromHistory(session.session_id)"
            >
              <div class="min-w-0">
                <p class="m-0 truncate font-bold leading-[1.35]">{{ session.title }}</p>
                <p class="mt-[0.28rem] mb-0 truncate text-[0.76rem] text-[color:var(--app-text-soft)]">
                  <template v-if="session.source === 'channel'">
                    <span>channel</span>
                    <span v-if="session.channel_meta?.platform">
                      · {{ session.channel_meta.platform }}</span>
                    <span v-if="session.channel_meta?.peer_id">
                      ({{ session.channel_meta.peer_id }})</span>
                  </template>
                  <template v-else>
                    <span>{{ session.source }}</span>
                    <span> · {{ session.backend_id }}</span>
                  </template>
                  <span> · </span>
                  {{ workspace.formatSessionUpdatedAt(session.updated_at) }}
                </p>
              </div>
            </button>
            <Button
              icon="pi pi-trash"
              class="session-history-delete shrink-0"
              text
              rounded
              severity="secondary"
              size="small"
              :loading="workspace.deletingSessionId === session.session_id"
              @click.stop="workspace.deleteSessionFromHistory(session.session_id)"
            />
          </div>
        </div>
      </ScrollPanel>

      <p
        v-else
        class="m-0 leading-[1.6] text-[color:var(--app-text-soft)]"
      >
        No saved sessions yet.
      </p>
    </div>

    <div
      class="grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Workspaces</p>
      <Select
        v-if="!workspace.isBooting"
        v-model="workspace.workspaceSurfaceModel"
        :options="workspace.workspaceSurfaceOptions"
        option-label="label"
        option-value="value"
        option-disabled="disabled"
        class="workspace-switcher-control w-full"
        size="small"
        aria-label="Switch workspace"
      />
      <div
        v-else
        class="flex min-h-[2.65rem] items-center rounded-2xl bg-[rgba(248,243,234,0.7)] px-[0.95rem] py-[0.78rem] text-[0.93rem] text-[color:var(--app-text-soft)]"
      >
        Loading workspace…
      </div>
    </div>
  </aside>
</template>

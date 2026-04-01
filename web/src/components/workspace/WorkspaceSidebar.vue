<script setup lang="ts">
import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

import CodexSessionExplorer from '../CodexSessionExplorer.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <aside
    v-if="!workspace.showCodexMobileChrome"
    class="flex min-h-0 flex-col gap-4 overflow-hidden max-[1023px]:order-2 max-[1023px]:gap-3 max-[1023px]:overflow-visible"
  >
    <div
      v-if="!workspace.isCodexWorkspace"
      class="brand-panel relative overflow-hidden rounded-[1.6rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.4rem] shadow-[var(--app-shadow)] backdrop-blur-[14px] after:pointer-events-none after:absolute after:top-[-3rem] after:right-[-3rem] after:h-40 after:w-40 after:rounded-full after:bg-[radial-gradient(circle,rgba(21,94,99,0.16),transparent_70%)] after:content-['']"
    >
      <p class="eyebrow">Local-first assistant</p>
      <h1 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[2.8rem] leading-none font-semibold">
        yier
      </h1>
      <p class="mt-3 mb-0 leading-[1.6] text-[color:var(--app-text-soft)]">
        Chat with your local agent, manage MCP connections, and keep everything anchored to your
        own machine.
      </p>
    </div>

    <div
      v-if="!workspace.isCodexWorkspace"
      class="side-card--status rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <div class="flex items-center justify-between gap-4">
        <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Session</span>
        <Tag :value="workspace.sessionLabel" rounded />
      </div>
      <div class="mt-[0.85rem] flex items-center justify-between gap-4">
        <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Frontend</span>
        <Tag
          :value="workspace.frontendMode"
          :severity="
            workspace.frontendMode === 'proxy'
              ? 'info'
              : workspace.frontendMode === 'static'
                ? 'success'
                : 'warn'
          "
          rounded
        />
      </div>
      <div class="mt-[0.85rem] flex items-center justify-between gap-4">
        <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">LLM</span>
        <Tag
          :value="workspace.llmReady ? 'Ready' : 'Needs setup'"
          :severity="workspace.llmReady ? 'success' : 'warn'"
          rounded
        />
      </div>
      <div class="mt-[0.85rem] flex items-center justify-between gap-4">
        <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Backend</span>
        <Tag
          :value="workspace.activeBackendId"
          :severity="workspace.activeBackendReady ? 'success' : 'warn'"
          rounded
        />
      </div>
    </div>

    <div v-if="!workspace.isBooting && !workspace.isCodexWorkspace" class="rail-actions grid gap-3">
      <select
        v-model="workspace.newSessionDraft.backendId"
        class="min-h-11 rounded-2xl border border-[color:var(--app-border)] bg-[rgba(255,252,245,0.92)] px-4 text-[color:var(--app-text)]"
      >
        <option
          v-for="backend in workspace.backendOptions"
          :key="backend.id"
          :value="backend.id"
        >
          {{ backend.label }}
        </option>
      </select>
      <input
        v-model="workspace.newSessionDraft.projectPath"
        class="min-h-11 rounded-2xl border border-[color:var(--app-border)] bg-[rgba(255,252,245,0.92)] px-4 text-[color:var(--app-text)]"
        type="text"
        placeholder="Project path for the next session"
      />
      <Button label="New Chat" icon="pi pi-plus" fluid @click="workspace.handleNewChatClick" />
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
        <Tag :value="String(workspace.sessionHistoryCount)" severity="secondary" rounded />
      </div>

      <ScrollPanel v-if="workspace.sessionHistory.length" class="min-h-0 flex-1">
        <div class="grid gap-[0.65rem] pr-[0.35rem]">
          <div
            v-for="session in workspace.sessionHistory"
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
                <p class="m-0 font-bold leading-[1.35]">{{ session.title }}</p>
                <p
                  v-if="session.preview"
                  class="mt-[0.18rem] mb-0 break-words text-[0.88rem] leading-[1.45] text-[color:var(--app-text-soft)]"
                >
                  {{ session.preview }}
                </p>
                <p class="mt-[0.28rem] mb-0 text-[0.76rem] text-[color:var(--app-text-soft)]">
                  <span>{{ session.source }}</span>
                  <span> · {{ session.backend_id }}</span>
                  <span v-if="session.project_path">
                    · {{ workspace.displayNameForPath(session.project_path) }}
                  </span>
                  <span v-if="session.channel_meta?.platform">
                    · {{ session.channel_meta.platform }}</span
                  >
                  <span v-if="session.channel_meta?.peer_id">
                    · {{ session.channel_meta.peer_id }}</span
                  >
                  <span> · </span>
                  {{ workspace.formatSessionUpdatedAt(session.updated_at) }}
                  <span v-if="session.message_count"> · {{ session.message_count }} msgs</span>
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

      <p v-else class="m-0 leading-[1.6] text-[color:var(--app-text-soft)]">
        No saved sessions yet.
      </p>
    </div>

    <div
      v-if="!workspace.isBooting && !workspace.isCodexWorkspace"
      class="side-card--nav grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <Button
        label="Chat"
        icon="pi pi-comment"
        fluid
        :outlined="!workspace.isChatRoute"
        :severity="workspace.isChatRoute ? undefined : 'secondary'"
        @click="workspace.openChat"
      />
      <Button
        label="Settings"
        icon="pi pi-sliders-h"
        fluid
        :outlined="!workspace.isSettingsRoute"
        :severity="workspace.isSettingsRoute ? undefined : 'secondary'"
        @click="workspace.openSettings"
      />
      <Button
        label="Channel"
        icon="pi pi-share-alt"
        fluid
        :outlined="!workspace.isChannelRoute"
        :severity="workspace.isChannelRoute ? undefined : 'secondary'"
        @click="workspace.openChannel"
      />
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

    <div
      v-if="!workspace.isBooting && !workspace.isCodexWorkspace"
      class="side-card--muted rounded-[1.3rem] border border-[color:var(--app-border)] bg-[rgba(247,243,232,0.88)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
    >
      <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Allowed roots</p>
      <ul class="mt-3 ml-4 p-0 leading-[1.5] text-[color:var(--app-text-soft)]">
        <li v-for="root in workspace.config?.allowed_roots ?? []" :key="root">{{ root }}</li>
      </ul>
    </div>
  </aside>
</template>

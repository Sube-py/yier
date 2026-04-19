<script setup lang="ts">
import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Select from 'primevue/select'
import Skeleton from 'primevue/skeleton'
import Tag from 'primevue/tag'

import CodexSessionExplorer from '../CodexSessionExplorer.vue'
import WorkspaceBrandPanel from './WorkspaceBrandPanel.vue'
import { useWorkspaceAppContext } from '../../composables/useWorkspaceApp'

withDefaults(
  defineProps<{
    surface?: 'rail' | 'drawer'
  }>(),
  {
    surface: 'rail',
  },
)

const workspace = useWorkspaceAppContext()
</script>

<template>
  <WorkspaceBrandPanel
    v-if="!workspace.isCodexCompactLayout"
    :variant="workspace.activeWorkspaceSurface === 'codex' ? 'codex' : 'yier'"
  />

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
  <div
    v-else-if="workspace.isBooting"
    class="rail-actions grid gap-3"
  >
    <div
      v-if="workspace.activeWorkspaceSurface === 'codex'"
      class="rounded-[1.2rem] border border-transparent bg-white/52 px-[0.82rem] py-[0.72rem]"
    >
      <div class="flex items-center justify-center gap-2.5">
        <Skeleton
          width="0.95rem"
          height="0.95rem"
          borderRadius="999px"
        />
        <Skeleton
          width="5.8rem"
          height="1.05rem"
          borderRadius="999px"
        />
      </div>
    </div>
    <div
      v-else
      class="rounded-[1.2rem] border border-transparent bg-white/40 px-[0.82rem] py-[0.72rem]"
    >
      <div class="flex items-center justify-center gap-2.5">
        <Skeleton
          width="0.95rem"
          height="0.95rem"
          borderRadius="999px"
        />
        <Skeleton
          width="4.9rem"
          height="1.05rem"
          borderRadius="999px"
        />
      </div>
    </div>
  </div>

  <CodexSessionExplorer
    v-if="
      !workspace.isBooting &&
      workspace.isCodexWorkspace &&
      (surface === 'drawer' || !workspace.isCodexCompactLayout)
    "
    :projects="workspace.activeCodexProjects"
    :active-session-id="workspace.activeSessionId"
    :opening-session-id="workspace.openingSessionId"
    :archiving-thread-id="workspace.archivingCodexThreadId"
    :active-session-status="workspace.activeSessionRuntime?.status ?? null"
    :active-project-path="workspace.activeProjectPath"
    :surface="surface === 'drawer' ? 'sheet' : 'sidebar'"
    @open-session="workspace.openCodexNativeSession"
    @archive-session="workspace.archiveCodexNativeSession"
    @start-session="workspace.handleCodexSessionStart"
  />
  <div
    v-else-if="workspace.isBooting"
    class="flex min-h-0 flex-1 flex-col gap-[0.85rem] overflow-hidden rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
  >
    <template v-if="workspace.activeWorkspaceSurface === 'codex'">
      <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl bg-white/32 px-[0.8rem] py-[0.1rem] pr-[0.1rem]">
        <Skeleton
          width="4.6rem"
          height="0.82rem"
          borderRadius="999px"
        />
        <Skeleton
          width="1.8rem"
          height="1.8rem"
          shape="circle"
        />
      </div>

      <div class="grid min-h-0 flex-1 gap-[0.55rem] overflow-x-hidden pr-[0.35rem]">
        <section
          v-for="projectIndex in 3"
          :key="projectIndex"
          class="grid gap-[0.2rem]"
        >
          <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl p-[0.1rem]">
            <div class="flex items-start gap-[0.65rem] rounded-[0.9rem] px-[0.7rem] py-[0.62rem]">
              <Skeleton
                width="0.82rem"
                height="0.82rem"
                borderRadius="999px"
                class="mt-[0.18rem]"
              />
              <Skeleton
                :width="projectIndex === 1 ? '7.2rem' : projectIndex === 2 ? '6rem' : '5.4rem'"
                height="1rem"
                borderRadius="999px"
              />
            </div>
            <Skeleton
              width="1.8rem"
              height="1.8rem"
              shape="circle"
            />
          </div>

          <div class="ml-[1.55rem] grid gap-[0.24rem]">
            <div
              v-for="threadIndex in (projectIndex === 1 ? 2 : 1)"
              :key="`${projectIndex}-${threadIndex}`"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-[0.8rem] rounded-[0.95rem] px-[0.5rem] py-[0.5rem] pr-[0.72rem]"
            >
              <div class="min-w-0 rounded-[0.7rem]">
                <Skeleton
                  :width="threadIndex === 1 ? '68%' : '56%'"
                  height="0.82rem"
                  borderRadius="999px"
                />
                <Skeleton
                  width="2.4rem"
                  height="0.72rem"
                  class="mt-[0.46rem]"
                  borderRadius="999px"
                />
              </div>
              <Skeleton
                width="2.1rem"
                height="0.78rem"
                borderRadius="999px"
              />
            </div>
          </div>
        </section>
      </div>
    </template>

    <template v-else>
      <div class="flex items-center justify-between gap-4">
        <Skeleton
          width="6.8rem"
          height="0.95rem"
          borderRadius="999px"
        />
        <Skeleton
          width="2.1rem"
          height="1.15rem"
          borderRadius="999px"
        />
      </div>

      <div class="grid min-h-0 flex-1 gap-[0.65rem] pr-[0.35rem]">
        <div
          v-for="index in 5"
          :key="index"
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] p-1"
        >
          <div class="rounded-[0.8rem] px-[0.65rem] py-[0.55rem]">
            <Skeleton
              width="72%"
              height="0.95rem"
              borderRadius="999px"
            />
            <Skeleton
              width="56%"
              height="0.78rem"
              class="mt-[0.42rem]"
              borderRadius="999px"
            />
          </div>
          <Skeleton
            width="1.6rem"
            height="1.6rem"
            shape="circle"
          />
        </div>
      </div>
    </template>
  </div>
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
            :disabled="workspace.isSwitchingSession"
            :aria-busy="workspace.openingSessionId === session.session_id"
            @click="workspace.openSessionFromHistory(session.session_id)"
          >
            <div class="min-w-0">
              <p class="m-0 truncate font-bold leading-[1.35]">{{ session.title }}</p>
              <div
                v-if="session.codex_goal_loop?.status && session.codex_goal_loop.status !== 'idle'"
                class="mt-[0.24rem]"
              >
                <span
                  class="inline-flex items-center rounded-full border border-[rgba(21,94,99,0.14)] bg-[rgba(214,238,234,0.82)] px-2 py-[0.16rem] text-[0.62rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-accent-deep)]"
                >
                  {{ session.codex_goal_loop.status }}
                </span>
              </div>
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
                <template v-if="workspace.openingSessionId === session.session_id">
                  <span class="inline-flex items-center gap-1 font-semibold text-[color:var(--app-accent-deep)]">
                    <i class="pi pi-spin pi-spinner text-[0.72rem]" />
                    <span>Loading</span>
                  </span>
                </template>
                <template v-else>
                  {{ workspace.formatSessionUpdatedAt(session.updated_at) }}
                </template>
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
    <Skeleton
      v-else
      width="100%"
      height="2.65rem"
      borderRadius="1rem"
    />
  </div>
</template>

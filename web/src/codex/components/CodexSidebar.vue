<script setup lang="ts">
import { computed } from 'vue'

import type { CodexProjectGroup, CodexWorkspaceResponse } from '../types'
import { displayPath, formatTimestamp, statusLabel, statusTone } from '../lib/format'

const projectPath = defineModel<string>('projectPath', { required: true })

const props = defineProps<{
  workspace: CodexWorkspaceResponse
  activeThreadId: string
  openingThreadId?: string
  archivingThreadId?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  selectThread: [threadId: string]
  startThread: [projectPath: string]
  archiveThread: [threadId: string]
  refresh: []
}>()

const projects = computed(() =>
  props.workspace.projects
    .map((project) => ({
      ...project,
      sessions: [...project.sessions].sort(
        (left, right) =>
          right.updated_at - left.updated_at ||
          right.started_at - left.started_at ||
          right.thread_id.localeCompare(left.thread_id),
      ),
    }))
    .sort(compareProjects),
)

function latestProjectTime(project: CodexProjectGroup) {
  return project.sessions.reduce(
    (latest, session) => Math.max(latest, session.updated_at || 0, session.started_at || 0),
    0,
  )
}

function compareProjects(left: CodexProjectGroup, right: CodexProjectGroup) {
  return (
    latestProjectTime(right) - latestProjectTime(left) ||
    (left.project || left.project_path).localeCompare(right.project || right.project_path)
  )
}

function startThread() {
  emit('startThread', projectPath.value)
}
</script>

<template>
  <aside class="flex min-h-0 flex-col border-r border-[color:var(--app-border)] bg-[rgba(255,253,247,0.82)]">
    <div class="grid gap-3 border-b border-[color:var(--app-border)] p-4">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--app-text-soft)]">
            Codex
          </p>
          <h1 class="m-0 truncate text-xl font-semibold text-[color:var(--app-text)]">
            Threads
          </h1>
        </div>
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[color:var(--app-border)] bg-white text-[color:var(--app-text-soft)] transition hover:text-[color:var(--app-text)]"
          aria-label="Refresh Codex threads"
          :disabled="busy"
          @click="emit('refresh')"
        >
          <i class="pi pi-refresh text-sm"></i>
        </button>
      </div>

      <div class="grid gap-2">
        <input
          v-model="projectPath"
          class="h-10 min-w-0 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm text-[color:var(--app-text)] outline-none transition focus:border-[color:var(--app-accent)]"
          placeholder="Project path"
        />
        <button
          type="button"
          class="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-[color:var(--app-accent)] px-3 text-sm font-semibold text-white transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-55"
          :disabled="busy"
          @click="startThread"
        >
          <i class="pi pi-plus text-xs"></i>
          <span>New thread</span>
        </button>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-3">
      <div v-if="!projects.length" class="grid place-items-center gap-2 p-6 text-center text-sm text-[color:var(--app-text-soft)]">
        <i class="pi pi-inbox text-lg"></i>
        <p class="m-0">No threads yet.</p>
      </div>

      <section
        v-for="project in projects"
        :key="project.project_path || project.project"
        class="mb-4 grid gap-1.5"
      >
        <div class="px-2 py-1">
          <p class="m-0 truncate text-sm font-semibold text-[color:var(--app-text)]">
            {{ project.project || displayPath(project.project_path) || 'Untitled project' }}
          </p>
          <p class="m-0 truncate text-[0.72rem] text-[color:var(--app-text-soft)]">
            {{ project.project_path }}
          </p>
        </div>

        <article
          v-for="thread in project.sessions"
          :key="thread.thread_id"
          class="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-lg border p-2 transition"
          :class="
            thread.thread_id === activeThreadId
              ? 'border-[rgba(21,94,99,0.24)] bg-[rgba(21,94,99,0.08)]'
              : 'border-transparent hover:border-[color:var(--app-border)] hover:bg-white/72'
          "
        >
          <button
            type="button"
            class="min-w-0 text-left"
            :disabled="busy || openingThreadId === thread.thread_id"
            @click="emit('selectThread', thread.thread_id)"
          >
            <span class="block truncate text-sm font-semibold text-[color:var(--app-text)]">
              {{ thread.title || thread.preview || thread.thread_id }}
            </span>
            <span class="mt-1 block truncate text-[0.76rem] text-[color:var(--app-text-soft)]">
              {{ thread.preview || thread.cwd }}
            </span>
            <span class="mt-2 flex min-w-0 items-center gap-2">
              <span
                class="inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[0.68rem] font-semibold"
                :class="statusTone(thread.status)"
              >
                {{ statusLabel(thread.status) }}
              </span>
              <span class="truncate text-[0.7rem] text-[color:var(--app-text-soft)]">
                {{ formatTimestamp(thread.updated_at || thread.started_at) }}
              </span>
            </span>
          </button>
          <button
            type="button"
            class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[color:var(--app-text-soft)] opacity-0 transition hover:bg-white hover:text-red-700 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
            aria-label="Archive Codex thread"
            :disabled="busy || archivingThreadId === thread.thread_id"
            @click="emit('archiveThread', thread.thread_id)"
          >
            <i class="pi pi-archive text-xs"></i>
          </button>
        </article>
      </section>
    </div>
  </aside>
</template>


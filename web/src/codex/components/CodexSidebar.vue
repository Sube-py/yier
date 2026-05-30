<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'

import type { CodexProjectGroup, CodexWorkspaceResponse } from '../types'
import { displayPath, formatTimestamp, statusLabel, statusTone } from '../lib/format'
import CodexHostPathPicker from './CodexHostPathPicker.vue'

const EXPANDED_PROJECTS_STORAGE_KEY = 'yier.codex.sidebar.expanded-projects'

const projectPath = defineModel<string>('projectPath', { required: true })

const props = defineProps<{
  workspace: CodexWorkspaceResponse
  activeThreadId: string
  openingThreadId?: string
  archivingThreadId?: string
  forkingThreadId?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  selectThread: [threadId: string]
  startThread: [projectPath: string]
  archiveThread: [threadId: string]
  forkThread: [threadId: string]
  copyError: [message: string]
  refresh: []
}>()

type ProjectWithKey = CodexProjectGroup & {
  key: string
}

const copiedThreadId = ref('')
const pathPickerVisible = ref(false)
const userExpandedProjects = ref<Record<string, boolean>>(readExpandedProjects())

const projects = computed<ProjectWithKey[]>(() =>
  props.workspace.projects
    .map((project) => ({
      ...project,
      key: projectKey(project),
      sessions: [...project.sessions].sort(
        (left, right) =>
          usedAt(right) - usedAt(left) ||
          right.started_at - left.started_at ||
          right.thread_id.localeCompare(left.thread_id),
      ),
    }))
    .sort(compareProjects),
)

const latestProjectKey = computed(() => projects.value[0]?.key ?? '')
const activeProjectKey = computed(() => {
  if (!props.activeThreadId) {
    return ''
  }
  return (
    projects.value.find((project) =>
      project.sessions.some((thread) => thread.thread_id === props.activeThreadId),
    )?.key ?? ''
  )
})

watch(
  userExpandedProjects,
  (value) => {
    persistExpandedProjects(value)
  },
  { deep: true },
)

watch(copiedThreadId, (threadId) => {
  if (!threadId) {
    return
  }
  window.setTimeout(() => {
    if (copiedThreadId.value === threadId) {
      copiedThreadId.value = ''
    }
  }, 1400)
})

function readExpandedProjects() {
  if (typeof localStorage === 'undefined') {
    return {}
  }
  try {
    const value = JSON.parse(
      localStorage.getItem(EXPANDED_PROJECTS_STORAGE_KEY) ?? '{}',
    )
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {}
    }
    return Object.fromEntries(
      Object.entries(value).filter(
        (entry): entry is [string, boolean] =>
          typeof entry[0] === 'string' && typeof entry[1] === 'boolean',
      ),
    )
  } catch {
    return {}
  }
}

function persistExpandedProjects(value: Record<string, boolean>) {
  if (typeof localStorage === 'undefined') {
    return
  }
  localStorage.setItem(EXPANDED_PROJECTS_STORAGE_KEY, JSON.stringify(value))
}

function usedAt(thread: { updated_at: number; started_at: number }) {
  return thread.updated_at || thread.started_at || 0
}

function latestProjectTime(project: CodexProjectGroup) {
  return project.sessions.reduce(
    (latest, session) => Math.max(latest, usedAt(session)),
    0,
  )
}

function compareProjects(left: CodexProjectGroup, right: CodexProjectGroup) {
  return (
    latestProjectTime(right) - latestProjectTime(left) ||
    (left.project || left.project_path).localeCompare(right.project || right.project_path)
  )
}

function projectKey(project: CodexProjectGroup) {
  return project.project_path || project.project || 'unknown'
}

function projectTitle(project: CodexProjectGroup) {
  return project.project || displayPath(project.project_path) || 'Untitled project'
}

function isProjectExpanded(project: ProjectWithKey) {
  if (project.key in userExpandedProjects.value) {
    return userExpandedProjects.value[project.key]
  }
  return project.key === activeProjectKey.value || project.key === latestProjectKey.value
}

function toggleProject(project: ProjectWithKey) {
  userExpandedProjects.value = {
    ...userExpandedProjects.value,
    [project.key]: !isProjectExpanded(project),
  }
}

function startThread() {
  const normalizedProjectPath = projectPath.value.trim()
  if (!normalizedProjectPath || props.busy) {
    return
  }
  emit('startThread', normalizedProjectPath)
}

function selectProjectPath(path: string) {
  projectPath.value = path
}

async function copyThreadId(threadId: string) {
  try {
    await navigator.clipboard.writeText(threadId)
    copiedThreadId.value = threadId
  } catch {
    emit('copyError', 'Unable to copy thread id.')
  }
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
        <div
          class="flex h-10 min-w-0 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm"
          data-codex-project-path-display
          :class="
            projectPath.trim()
              ? 'text-[color:var(--app-text)]'
              : 'text-[color:var(--app-text-soft)]'
          "
        >
          <i class="pi pi-folder text-xs text-[color:var(--app-text-soft)]"></i>
          <span class="truncate">{{ projectPath.trim() || 'Select project folder' }}</span>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <Button
            label="Choose folder"
            icon="pi pi-folder-open"
            severity="secondary"
            outlined
            class="h-10 min-w-0"
            data-codex-choose-project-folder
            :disabled="busy"
            @click="pathPickerVisible = true"
          />
          <Button
            label="New thread"
            icon="pi pi-plus"
            class="h-10 min-w-0"
            data-codex-start-thread
            :disabled="busy || !projectPath.trim()"
            @click="startThread"
          />
        </div>

        <CodexHostPathPicker
          v-model:visible="pathPickerVisible"
          :selected-path="projectPath"
          :disabled="busy"
          @select="selectProjectPath"
        />
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-3">
      <div v-if="!projects.length" class="grid place-items-center gap-2 p-6 text-center text-sm text-[color:var(--app-text-soft)]">
        <i class="pi pi-inbox text-lg"></i>
        <p class="m-0">No threads yet.</p>
      </div>

      <section
        v-for="project in projects"
        :key="project.key"
        class="mb-3 grid gap-1.5"
      >
        <button
          type="button"
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-white/65"
          :aria-expanded="isProjectExpanded(project)"
          @click="toggleProject(project)"
        >
          <span class="min-w-0">
            <span class="flex min-w-0 items-center gap-2">
              <span class="truncate text-sm font-semibold text-[color:var(--app-text)]">
                {{ projectTitle(project) }}
              </span>
              <span class="shrink-0 rounded-full bg-[rgba(21,94,99,0.08)] px-2 py-0.5 text-[0.68rem] font-semibold text-[color:var(--app-text-soft)]">
                {{ project.session_count }}
              </span>
            </span>
            <span class="mt-0.5 block truncate text-[0.72rem] text-[color:var(--app-text-soft)]">
              {{ project.project_path || 'No project path' }}
            </span>
          </span>
          <i
            class="pi text-xs text-[color:var(--app-text-soft)]"
            :class="isProjectExpanded(project) ? 'pi-chevron-down' : 'pi-chevron-right'"
          ></i>
        </button>

        <div v-show="isProjectExpanded(project)" class="grid gap-1">
          <article
            v-for="thread in project.sessions"
            :key="thread.thread_id"
            class="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-lg border px-2 py-2 transition focus-within:border-[color:var(--app-border)] focus-within:bg-white/80"
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

            <div class="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
              <button
                type="button"
                class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[color:var(--app-text-soft)] transition hover:bg-white hover:text-[color:var(--app-accent)] disabled:opacity-40"
                :aria-label="`Fork Codex thread ${thread.thread_id}`"
                :title="`Fork ${thread.thread_id}`"
                :disabled="busy || forkingThreadId === thread.thread_id"
                @click="emit('forkThread', thread.thread_id)"
              >
                <i
                  class="pi text-xs"
                  :class="forkingThreadId === thread.thread_id ? 'pi-spinner pi-spin' : 'pi-sitemap'"
                ></i>
              </button>
              <button
                type="button"
                class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[color:var(--app-text-soft)] transition hover:bg-white hover:text-[color:var(--app-text)] disabled:opacity-40"
                :aria-label="copiedThreadId === thread.thread_id ? 'Copied thread id' : `Copy thread id ${thread.thread_id}`"
                :title="copiedThreadId === thread.thread_id ? 'Copied' : 'Copy thread id'"
                @click="copyThreadId(thread.thread_id)"
              >
                <i
                  class="pi text-xs"
                  :class="copiedThreadId === thread.thread_id ? 'pi-check' : 'pi-copy'"
                ></i>
              </button>
              <button
                type="button"
                class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[color:var(--app-text-soft)] transition hover:bg-white hover:text-red-700 disabled:opacity-40"
                aria-label="Archive Codex thread"
                title="Archive thread"
                :disabled="busy || archivingThreadId === thread.thread_id"
                @click="emit('archiveThread', thread.thread_id)"
              >
                <i
                  class="pi text-xs"
                  :class="archivingThreadId === thread.thread_id ? 'pi-spinner pi-spin' : 'pi-archive'"
                ></i>
              </button>
            </div>
          </article>
        </div>
      </section>
    </div>
  </aside>
</template>

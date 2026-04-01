<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'

import { apiPost } from '../lib/api'
import type { CodexProjectGroup, SelectDirectoryResponse } from '../types/api'

const props = withDefaults(
  defineProps<{
    projects: CodexProjectGroup[]
    activeSessionId: string
    activeSessionStatus?: string | null
    activeProjectPath?: string
    surface?: 'sidebar' | 'sheet'
    closeable?: boolean
  }>(),
  {
    activeSessionStatus: null,
    activeProjectPath: '',
    surface: 'sidebar',
    closeable: false,
  },
)

const emit = defineEmits<{
  openSession: [threadId: string]
  startSession: [projectPath: string]
  close: []
}>()

const expandedProjectPaths = ref<string[]>([])
const isSheet = computed(() => props.surface === 'sheet')

const sortedProjects = computed(() =>
  props.projects
    .map((project) => ({
      ...project,
      sessions: [...project.sessions].sort(compareSessionsByRecentActivity),
    }))
    .sort(compareProjectsByRecentActivity),
)

watch(
  () => [sortedProjects.value, props.activeSessionId],
  () => {
    syncExpandedProjects()
  },
  { deep: true, immediate: true },
)

function compareSessionsByRecentActivity(
  left: CodexProjectGroup['sessions'][number],
  right: CodexProjectGroup['sessions'][number],
) {
  return (
    right.updated_at - left.updated_at ||
    right.started_at - left.started_at ||
    right.thread_id.localeCompare(left.thread_id)
  )
}

function compareProjectNames(left: CodexProjectGroup, right: CodexProjectGroup) {
  const leftLabel = projectLabel(left).toLocaleLowerCase()
  const rightLabel = projectLabel(right).toLocaleLowerCase()
  return leftLabel.localeCompare(rightLabel) || left.project_path.localeCompare(right.project_path)
}

function compareProjectsByRecentActivity(left: CodexProjectGroup, right: CodexProjectGroup) {
  return (
    latestProjectUpdatedAt(right) - latestProjectUpdatedAt(left) ||
    compareProjectNames(left, right)
  )
}

function latestProjectUpdatedAt(project: CodexProjectGroup) {
  return project.sessions.reduce(
    (latest, session) => Math.max(latest, session.updated_at || 0, session.started_at || 0),
    0,
  )
}

function formatTimestamp(timestamp: number) {
  if (!timestamp) {
    return ''
  }

  const deltaSeconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp))
  if (deltaSeconds < 60) {
    return 'Just now'
  }
  if (deltaSeconds < 3600) {
    return `${Math.floor(deltaSeconds / 60)}m`
  }
  if (deltaSeconds < 86400) {
    return `${Math.floor(deltaSeconds / 3600)}h`
  }
  if (deltaSeconds < 604800) {
    return `${Math.floor(deltaSeconds / 86400)}d`
  }
  return new Date(timestamp * 1000).toLocaleDateString()
}

function projectContainsActiveSession(project: CodexProjectGroup) {
  return project.sessions.some((session) => session.thread_id === props.activeSessionId)
}

function syncExpandedProjects() {
  if (!sortedProjects.value.length) {
    expandedProjectPaths.value = []
    return
  }

  const availableProjectPaths = new Set(sortedProjects.value.map((project) => project.project_path))
  const nextExpanded = expandedProjectPaths.value.filter((projectPath) =>
    availableProjectPaths.has(projectPath),
  )
  const activeProject = sortedProjects.value.find((project) => projectContainsActiveSession(project))

  if (activeProject && !nextExpanded.includes(activeProject.project_path)) {
    nextExpanded.unshift(activeProject.project_path)
  }

  if (!nextExpanded.length) {
    nextExpanded.push(activeProject?.project_path ?? sortedProjects.value[0]!.project_path)
  }

  expandedProjectPaths.value = [...new Set(nextExpanded)]
}

function isProjectExpanded(projectPath: string) {
  return expandedProjectPaths.value.includes(projectPath)
}

function toggleProject(projectPath: string) {
  if (isProjectExpanded(projectPath)) {
    expandedProjectPaths.value = expandedProjectPaths.value.filter((item) => item !== projectPath)
    return
  }
  expandedProjectPaths.value = [...expandedProjectPaths.value, projectPath]
}

function pathDisplayName(path: string) {
  const parts = path.split('/').filter(Boolean)
  if (!parts.length) {
    return path
  }
  return parts[parts.length - 1] ?? path
}

function projectLabel(project: CodexProjectGroup) {
  return project.project || pathDisplayName(project.project_path) || 'Untitled project'
}

function requestClose() {
  emit('close')
}

function startSessionForProject(projectPath?: string) {
  emit('startSession', projectPath?.trim() || props.activeProjectPath?.trim() || '')
  if (isSheet.value) {
    requestClose()
  }
}

function openSession(threadId: string) {
  emit('openSession', threadId)
  if (isSheet.value) {
    requestClose()
  }
}

const selectNewProject = async () => {
  try {
    const payload = await apiPost<SelectDirectoryResponse>('/api/system/select-directory', {
      initial_path: props.activeProjectPath?.trim() || null,
    })
    if (!payload.selected) {
      return
    }

    const projectPath = payload.project_path.trim()
    if (!projectPath) {
      return
    }

    emit('startSession', projectPath)
    if (isSheet.value) {
      requestClose()
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to select a project directory.'
    window.alert(message)
  }
}
</script>

<template>
  <div
    :class="
      isSheet
        ? 'flex min-h-0 flex-1 flex-col gap-4 overflow-hidden'
        : 'flex min-h-0 flex-1 flex-col gap-[0.85rem] overflow-hidden rounded-[1.3rem] border border-[color:var(--app-border)] bg-[linear-gradient(180deg,rgba(255,252,247,0.95),rgba(245,239,228,0.82)),rgba(255,252,245,0.84)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]'
    "
  >
    <div
      v-if="isSheet"
      class="flex flex-col gap-3 border-b border-[color:var(--app-border)] pb-3"
    >
      <div class="sheet-handle"></div>
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="eyebrow">Codex workspace</p>
          <h4>Threads</h4>
        </div>
        <Button
          v-if="closeable"
          icon="pi pi-times"
          rounded
          text
          severity="secondary"
          aria-label="Close threads panel"
          @click="requestClose"
        />
      </div>
    </div>

    <div class="flex items-stretch gap-[0.85rem]">
      <Button
        label="New thread"
        icon="pi pi-pen-to-square"
        class="w-full"
        :pt="{
          root: {
            class:
              'justify-start gap-2.5 border border-transparent bg-white/52 px-[0.82rem] py-[0.72rem] text-[color:var(--app-text)] shadow-none',
          },
          label: {
            class: 'flex-1 text-left font-semibold',
          },
          icon: {
            class: 'm-0',
          },
        }"
        @click="startSessionForProject()"
      />
    </div>

    <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl bg-white/32 px-[0.8rem] py-[0.1rem] pr-[0.1rem]">
      <p class="m-0 text-xs font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
        Threads
      </p>
      <Button
        rounded
        text
        icon="pi pi-folder-plus"
        class="codex-select-project-action text-[color:var(--app-text-soft)]"
        aria-label="Open thread actions"
        @click="selectNewProject"
      />
    </div>

    <ScrollPanel
      v-if="sortedProjects.length"
      class="min-h-0 flex-1"
    >
      <div class="grid gap-[0.55rem] overflow-x-hidden pr-[0.35rem]">
        <section
          v-for="project in sortedProjects"
          :key="project.project_path"
          class="grid gap-[0.2rem]"
          :class="{ 'pb-[0.15rem]': isProjectExpanded(project.project_path) }"
        >
          <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl p-[0.1rem] transition duration-150 hover:bg-white/44 focus-within:bg-white/44">
            <button
              type="button"
              class="codex-project-toggle flex w-full items-start gap-[0.65rem] rounded-[0.9rem] px-[0.7rem] py-[0.62rem] text-left transition duration-150 hover:bg-white/36"
              :class="{ 'bg-white/36': isProjectExpanded(project.project_path) }"
              :aria-expanded="isProjectExpanded(project.project_path)"
              :title="project.project_path"
              @click="toggleProject(project.project_path)"
            >
              <span
                class="relative mt-[0.12rem] inline-flex h-[1.2rem] w-4 flex-none items-center justify-center"
                aria-hidden="true"
              >
                <i
                  class="pi absolute inset-0 inline-flex items-center justify-center text-[0.92rem] leading-none text-[color:var(--app-text-soft)] transition duration-150"
                  :class="isProjectExpanded(project.project_path) ? 'pi-chevron-down' : 'pi-chevron-right'"
                />
              </span>
              <div class="min-w-0">
                <p class="codex-project-title m-0 text-base leading-[1.3] font-bold">
                  {{ projectLabel(project) }}
                </p>
              </div>
            </button>

            <div class="flex items-center justify-end">
              <Button
                rounded
                text
                icon="pi pi-pen-to-square"
                class="codex-project-start-action"
                :title="`Start a new chat in ${projectLabel(project)}`"
                @click.stop="startSessionForProject(project.project_path)"
              />
            </div>
          </div>

          <div
            v-if="isProjectExpanded(project.project_path)"
            class="ml-[1.55rem] grid gap-[0.24rem]"
          >
            <button
              v-for="session in project.sessions"
              :key="session.thread_id"
              type="button"
              class="codex-session-item block w-full overflow-hidden rounded-[0.95rem] px-[0.5rem] py-[0.5rem] pr-[0.72rem] text-left transition duration-150 hover:bg-white/48 focus-visible:bg-white/48"
              :class="{
                'bg-[rgba(232,244,241,0.72)] shadow-[inset_3px_0_0_rgba(21,94,99,0.52)]':
                  session.thread_id === activeSessionId,
              }"
              :title="session.cwd || session.title"
              @click="openSession(session.thread_id)"
            >
              <div class="block min-w-0 max-w-full overflow-hidden">
                <div class="grid w-full min-w-0 max-w-full grid-cols-[minmax(0,1fr)_auto] items-baseline gap-[0.8rem] overflow-hidden">
                  <p
                    class="codex-session-title block min-w-0 max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[0.8rem] leading-[1.4]"
                    :title="session.title"
                  >
                    {{ session.title }}
                  </p>
                  <p class="shrink-0 whitespace-nowrap text-right text-[0.72rem] leading-[1.35] text-[color:var(--app-text-soft)]">
                    <span>{{ formatTimestamp(session.updated_at) }}</span>
                  </p>
                </div>
              </div>
            </button>
          </div>
        </section>
      </div>
    </ScrollPanel>

    <p
      v-else
      class="m-0 text-[color:var(--app-text-soft)] leading-[1.6]"
    >No native Codex sessions found in this machine yet.</p>
  </div>
</template>

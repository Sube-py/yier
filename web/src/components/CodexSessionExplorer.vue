<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'

import type { CodexProjectGroup } from '../types/api'

const props = defineProps<{
  projects: CodexProjectGroup[]
  activeSessionId: string
  activeSessionStatus?: string | null
  activeProjectPath?: string
}>()

const emit = defineEmits<{
  openSession: [threadId: string]
  startSession: [projectPath: string]
}>()

const expandedProjectPaths = ref<string[]>([])

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

function startNewChat(projectPath?: string) {
  emit('startSession', projectPath?.trim() || props.activeProjectPath?.trim() || '')
}
</script>

<template>
  <div class="side-card side-card--history side-card--codex">
    <div class="codex-sidebar-toolbar">
      <div>
        <p class="side-card-label">Codex workspace</p>
        <p class="codex-sidebar-copy">Native projects and active sessions</p>
      </div>
      <Button
        label="New chat"
        icon="pi pi-plus"
        class="codex-toolbar-primary"
        @click="startNewChat()"
      />
    </div>

    <ScrollPanel
      v-if="sortedProjects.length"
      class="session-history-scroll"
    >
      <div class="codex-project-list">
        <section
          v-for="project in sortedProjects"
          :key="project.project_path"
          class="codex-project-group"
          :class="{ 'codex-project-group--expanded': isProjectExpanded(project.project_path) }"
        >
          <div class="codex-project-row">
            <button
              type="button"
              class="codex-project-toggle"
              :class="{ 'codex-project-toggle--expanded': isProjectExpanded(project.project_path) }"
              :aria-expanded="isProjectExpanded(project.project_path)"
              :title="project.project_path"
              @click="toggleProject(project.project_path)"
            >
              <span
                class="codex-project-chevron"
                aria-hidden="true"
              >
                {{ isProjectExpanded(project.project_path) ? '▾' : '▸' }}
              </span>
              <div class="codex-project-toggle-copy">
                <p class="codex-project-title">{{ projectLabel(project) }}</p>
              </div>
            </button>

            <div class="codex-project-actions">
              <Button
                rounded
                text
                icon="pi pi-plus"
                class="codex-project-action"
                :title="`Start a new chat in ${projectLabel(project)}`"
                @click.stop="startNewChat(project.project_path)"
              />
            </div>
          </div>

          <div
            v-if="isProjectExpanded(project.project_path)"
            class="codex-session-tree"
          >
            <button
              v-for="session in project.sessions"
              :key="session.thread_id"
              type="button"
              class="codex-session-item"
              :class="{ 'codex-session-item--active': session.thread_id === activeSessionId }"
              :title="session.cwd || session.title"
              @click="emit('openSession', session.thread_id)"
            >
              <div class="codex-session-copy">
                <div class="codex-session-topline">
                  <p
                    class="codex-session-title"
                    :title="session.title"
                  >
                    {{ session.title }}
                  </p>
                  <p class="codex-session-meta">
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
      class="side-card-empty"
    >No native Codex sessions found in this machine yet.</p>
  </div>
</template>

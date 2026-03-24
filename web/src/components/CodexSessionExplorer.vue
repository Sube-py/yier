<script setup lang="ts">
import { ref, watch } from 'vue'

import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'

import type { CodexProjectGroup } from '../types/api'

const props = defineProps<{
  projects: CodexProjectGroup[]
  activeSessionId: string
}>()

const emit = defineEmits<{
  openSession: [threadId: string]
}>()

const expandedProjectPaths = ref<string[]>([])

function formatTimestamp(timestamp: number) {
  if (!timestamp) {
    return ''
  }
  return new Date(timestamp * 1000).toLocaleString()
}

function projectContainsActiveSession(project: CodexProjectGroup) {
  return project.sessions.some((session) => session.thread_id === props.activeSessionId)
}

function syncExpandedProjects() {
  if (!props.projects.length) {
    expandedProjectPaths.value = []
    return
  }

  const availableProjectPaths = new Set(props.projects.map((project) => project.project_path))
  const nextExpanded = expandedProjectPaths.value.filter((projectPath) =>
    availableProjectPaths.has(projectPath),
  )
  const activeProject = props.projects.find((project) => projectContainsActiveSession(project))

  if (activeProject && !nextExpanded.includes(activeProject.project_path)) {
    nextExpanded.push(activeProject.project_path)
  }

  if (!nextExpanded.length) {
    nextExpanded.push(activeProject?.project_path ?? props.projects[0]!.project_path)
  }

  expandedProjectPaths.value = nextExpanded
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

watch(
  () => [props.projects, props.activeSessionId],
  () => {
    syncExpandedProjects()
  },
  { deep: true, immediate: true },
)
</script>

<template>
  <div class="side-card side-card--history side-card--codex">
    <div class="side-card-row">
      <div>
        <p class="side-card-label">Codex workspace</p>
        <p class="codex-sidebar-copy">Native projects and active sessions</p>
      </div>
      <Tag :value="String(projects.length)" severity="secondary" rounded />
    </div>

    <ScrollPanel v-if="projects.length" class="session-history-scroll">
      <div class="codex-project-list">
        <section v-for="project in projects" :key="project.project_path" class="codex-project-group">
          <button
            type="button"
            class="codex-project-toggle"
            :class="{ 'codex-project-toggle--expanded': isProjectExpanded(project.project_path) }"
            :aria-expanded="isProjectExpanded(project.project_path)"
            @click="toggleProject(project.project_path)"
          >
            <div class="codex-project-toggle-copy">
              <p class="codex-project-title">{{ project.project }}</p>
              <p class="codex-project-path">{{ project.project_path }}</p>
            </div>
            <div class="codex-project-toggle-meta">
              <Tag :value="String(project.session_count)" severity="contrast" rounded />
              <span class="codex-project-chevron">
                {{ isProjectExpanded(project.project_path) ? '▾' : '▸' }}
              </span>
            </div>
          </button>

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
              @click="emit('openSession', session.thread_id)"
            >
              <span class="codex-session-branch" aria-hidden="true"></span>
              <div class="codex-session-copy">
                <p class="codex-session-title">{{ session.title }}</p>
                <p class="codex-session-preview">{{ session.preview }}</p>
                <p class="codex-session-meta">
                  <span>{{ formatTimestamp(session.updated_at) }}</span>
                  <span v-if="session.cwd"> · {{ session.cwd }}</span>
                </p>
              </div>
            </button>
          </div>
        </section>
      </div>
    </ScrollPanel>

    <p v-else class="side-card-empty">No native Codex sessions found in this machine yet.</p>
  </div>
</template>

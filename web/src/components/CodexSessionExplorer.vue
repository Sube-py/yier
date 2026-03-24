<script setup lang="ts">
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'

import type { CodexProjectGroup } from '../types/api'

defineProps<{
  projects: CodexProjectGroup[]
  activeSessionId: string
}>()

const emit = defineEmits<{
  openSession: [threadId: string]
}>()

function formatTimestamp(timestamp: number) {
  if (!timestamp) {
    return ''
  }
  return new Date(timestamp * 1000).toLocaleString()
}
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
          <div class="codex-project-header">
            <div>
              <p class="codex-project-title">{{ project.project }}</p>
              <p class="codex-project-path">{{ project.project_path }}</p>
            </div>
            <Tag :value="String(project.session_count)" severity="contrast" rounded />
          </div>

          <button
            v-for="session in project.sessions"
            :key="session.thread_id"
            type="button"
            class="codex-session-item"
            :class="{ 'codex-session-item--active': session.thread_id === activeSessionId }"
            @click="emit('openSession', session.thread_id)"
          >
            <div class="codex-session-copy">
              <p class="codex-session-title">{{ session.title }}</p>
              <p class="codex-session-preview">{{ session.preview }}</p>
              <p class="codex-session-meta">
                <span>{{ formatTimestamp(session.updated_at) }}</span>
                <span v-if="session.cwd"> · {{ session.cwd }}</span>
              </p>
            </div>
          </button>
        </section>
      </div>
    </ScrollPanel>

    <p v-else class="side-card-empty">No native Codex sessions found in this machine yet.</p>
  </div>
</template>

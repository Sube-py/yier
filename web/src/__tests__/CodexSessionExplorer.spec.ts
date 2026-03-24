import { describe, expect, it, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

import CodexSessionExplorer from '../components/CodexSessionExplorer.vue'
import type { CodexProjectGroup } from '../types/api'

const projects: CodexProjectGroup[] = [
  {
    project: 'zeta-service',
    project_path: '/tmp/zeta-service',
    session_count: 1,
    sessions: [
      {
        thread_id: 'thread-zeta',
        title: 'Zeta thread',
        preview: 'Latest work in zeta',
        updated_at: 100,
        started_at: 90,
        status: 'idle',
        cwd: '/tmp/zeta-service',
        project: 'zeta-service',
        project_path: '/tmp/zeta-service',
        source: 'active',
      },
    ],
  },
  {
    project: 'alpha-service',
    project_path: '/tmp/alpha-service',
    session_count: 2,
    sessions: [
      {
        thread_id: 'thread-alpha-2',
        title: 'Alpha second thread',
        preview: 'Newest alpha work',
        updated_at: 300,
        started_at: 200,
        status: 'active',
        cwd: '/tmp/alpha-service',
        project: 'alpha-service',
        project_path: '/tmp/alpha-service',
        source: 'active',
      },
      {
        thread_id: 'thread-alpha-1',
        title: 'Alpha first thread',
        preview: 'Older alpha work',
        updated_at: 150,
        started_at: 120,
        status: 'idle',
        cwd: '/tmp/alpha-service/packages/api',
        project: 'alpha-service',
        project_path: '/tmp/alpha-service',
        source: 'active',
      },
    ],
  },
]

function mountExplorer(
  overrides: Partial<{
    projects: CodexProjectGroup[]
    activeSessionId: string
    activeSessionStatus: string
    activeProjectPath: string
  }> = {},
) {
  return mount(CodexSessionExplorer, {
    props: {
      projects,
      activeSessionId: 'thread-alpha-2',
      activeSessionStatus: 'active',
      activeProjectPath: '/tmp/alpha-service',
      ...overrides,
    },
    global: {
      stubs: {
        Button: {
          inheritAttrs: false,
          props: ['label', 'icon'],
          template:
            '<button type="button" v-bind="$attrs"><span v-if="icon">{{ icon }}</span><span v-if="label">{{ label }}</span><slot /></button>',
        },
        ScrollPanel: {
          template: '<div class="scroll-panel-stub"><slot /></div>',
        },
      },
    },
  })
}

describe('CodexSessionExplorer', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('orders projects by recent activity by default', async () => {
    const wrapper = mountExplorer()

    expect(
      wrapper.findAll('.codex-project-title').map((node) => node.text()),
    ).toEqual(['alpha-service', 'zeta-service'])
  })

  it('expands the active project, toggles visibility, and emits actions', async () => {
    const wrapper = mountExplorer()

    expect(wrapper.findAll('.codex-session-item')).toHaveLength(2)
    expect(wrapper.find('.codex-session-title').attributes('title')).toBe('Alpha second thread')

    const projectAction = wrapper.find('.codex-project-start-action')
    await projectAction.trigger('click')
    expect(wrapper.emitted('startSession')).toEqual([['/tmp/alpha-service']])

    const sessionItems = wrapper.findAll('.codex-session-item')
    await sessionItems[1]!.trigger('click')
    expect(wrapper.emitted('openSession')).toEqual([['thread-alpha-1']])

    const firstProjectToggle = wrapper.find('.codex-project-toggle')
    await firstProjectToggle.trigger('click')
    expect(wrapper.findAll('.codex-session-item')).toHaveLength(0)

    await firstProjectToggle.trigger('click')
    expect(wrapper.findAll('.codex-session-item')).toHaveLength(2)
  })
})

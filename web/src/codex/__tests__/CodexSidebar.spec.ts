import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PrimeVue from 'primevue/config'

import CodexSidebar from '../components/CodexSidebar.vue'
import type { CodexWorkspaceResponse } from '../types'

function thread(
  threadId: string,
  project: string,
  projectPath: string,
  updatedAt: number,
) {
  return {
    thread_id: threadId,
    title: threadId,
    preview: `${threadId} preview`,
    updated_at: updatedAt,
    started_at: updatedAt - 1,
    status: 'idle',
    cwd: projectPath,
    project,
    project_path: projectPath,
    source: 'appServer',
  }
}

function workspace(): CodexWorkspaceResponse {
  return {
    projects: [
      {
        project: 'beta',
        project_path: '/tmp/beta',
        session_count: 1,
        sessions: [thread('thread-beta', 'beta', '/tmp/beta', 10)],
      },
      {
        project: 'alpha',
        project_path: '/tmp/alpha',
        session_count: 2,
        sessions: [
          thread('thread-alpha-old', 'alpha', '/tmp/alpha', 15),
          thread('thread-alpha-new', 'alpha', '/tmp/alpha', 30),
        ],
      },
    ],
    paired_editors: [],
  }
}

function mountSidebar(props: Partial<InstanceType<typeof CodexSidebar>['$props']> = {}) {
  let wrapper: ReturnType<typeof mount>
  wrapper = mount(CodexSidebar, {
    props: {
      projectPath: '',
      workspace: workspace(),
      activeThreadId: '',
      ...props,
      'onUpdate:projectPath': (value: string) =>
        wrapper.setProps({ projectPath: value }),
    },
    global: {
      plugins: [PrimeVue],
      stubs: {
        CodexHostPathPicker: {
          props: ['visible', 'selectedPath', 'disabled'],
          emits: ['update:visible', 'select'],
          template:
            '<div data-codex-host-path-picker-stub><button data-codex-picker-select @click="$emit(\'select\', \'/tmp/selected\')">Select</button></div>',
        },
      },
    },
  })
  return wrapper
}

describe('CodexSidebar', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  it('sorts project groups and threads by latest usage time', () => {
    const wrapper = mountSidebar()
    const projectButtons = wrapper.findAll('button[aria-expanded]')

    expect(projectButtons[0]?.text()).toContain('alpha')
    expect(projectButtons[1]?.text()).toContain('beta')

    const text = wrapper.text()
    expect(text.indexOf('thread-alpha-new')).toBeLessThan(
      text.indexOf('thread-alpha-old'),
    )
  })

  it('expands the latest project and the active thread project by default', () => {
    const wrapper = mountSidebar({ activeThreadId: 'thread-beta' })
    const projectButtons = wrapper.findAll('button[aria-expanded]')

    expect(projectButtons[0]?.attributes('aria-expanded')).toBe('true')
    expect(projectButtons[1]?.attributes('aria-expanded')).toBe('true')
    expect(wrapper.text()).toContain('thread-alpha-new')
    expect(wrapper.text()).toContain('thread-beta')
  })

  it('persists project collapse toggles', async () => {
    const wrapper = mountSidebar()
    const alphaButton = wrapper.findAll('button[aria-expanded]')[0]!

    await alphaButton.trigger('click')

    expect(alphaButton.attributes('aria-expanded')).toBe('false')
    expect(JSON.parse(localStorage.getItem('yier.codex.sidebar.expanded-projects') ?? '{}')).toEqual({
      '/tmp/alpha': false,
    })
  })

  it('uses a selected host folder instead of a manual path input', async () => {
    const wrapper = mountSidebar()

    expect(wrapper.find('input[placeholder="Project path"]').exists()).toBe(false)
    expect(wrapper.get('[data-codex-project-path-display]').text()).toContain(
      'Select project folder',
    )
    expect(wrapper.get('[data-codex-start-thread]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-codex-picker-select]').trigger('click')

    expect(wrapper.get('[data-codex-project-path-display]').text()).toContain(
      '/tmp/selected',
    )
    await wrapper.get('[data-codex-start-thread]').trigger('click')

    expect(wrapper.emitted('startThread')).toEqual([['/tmp/selected']])
  })

  it('emits fork and archive actions from hover controls', async () => {
    const wrapper = mountSidebar()

    await wrapper
      .get('button[aria-label="Fork Codex thread thread-alpha-new"]')
      .trigger('click')
    await wrapper.get('button[aria-label="Archive Codex thread"]').trigger('click')

    expect(wrapper.emitted('forkThread')).toEqual([['thread-alpha-new']])
    expect(wrapper.emitted('archiveThread')).toEqual([['thread-alpha-new']])
  })

  it('copies thread ids and marks the copied row', async () => {
    const wrapper = mountSidebar()

    await wrapper
      .get('button[aria-label="Copy thread id thread-alpha-new"]')
      .trigger('click')

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('thread-alpha-new')
    expect(wrapper.find('button[aria-label="Copied thread id"]').exists()).toBe(true)
  })

  it('emits a copy error when clipboard access fails', async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error('blocked'))
    const wrapper = mountSidebar()

    await wrapper
      .get('button[aria-label="Copy thread id thread-alpha-new"]')
      .trigger('click')

    expect(wrapper.emitted('copyError')).toEqual([['Unable to copy thread id.']])
  })
})

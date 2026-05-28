import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import CodexView from '../../views/CodexView.vue'
import { createTestRouter } from '../../router'

const workspaceMock = {
  activeMode: 'build',
  activeStatus: 'idle',
  activeThreadId: '',
  activeThreadState: null,
  activeUserInputRequest: null,
  archivingThreadId: '',
  archiveThread: vi.fn(),
  compactThread: vi.fn(),
  enqueueFollowup: vi.fn(),
  errorMessage: '',
  forkingThreadId: '',
  forkThread: vi.fn(),
  isActiveTurnInProgress: false,
  isArchiving: false,
  isBooting: false,
  isCommandBusy: false,
  isRenaming: false,
  openingThreadId: '',
  projectPathDraft: '',
  queuedFollowups: [],
  refreshWorkspace: vi.fn(),
  removeFollowup: vi.fn(),
  renameThread: vi.fn(),
  resumeEmbedThread: vi.fn(),
  selectThread: vi.fn(),
  sendPrompt: vi.fn(),
  setMode: vi.fn(),
  startEmbedThread: vi.fn(),
  startThread: vi.fn(),
  status: 'idle',
  steerPrompt: vi.fn(),
  submitUserInputResponse: vi.fn(),
  successMessage: '',
  workspace: { paired_editors: [], projects: [] },
  interruptTurn: vi.fn(),
}

vi.mock('../composables/useCodexWorkspace', () => ({
  useCodexWorkspace: () => workspaceMock,
}))

function mountCodexView(router: ReturnType<typeof createTestRouter>) {
  return shallowMount(CodexView, {
    global: {
      plugins: [router],
      stubs: {
        CodexComposer: true,
        CodexConversation: true,
        CodexChatPane: true,
        CodexRequestPanel: true,
        CodexSidebar: true,
        CodexThreadToolbar: true,
        RouterLink: false,
        Teleport: true,
        Transition: true,
      },
    },
  })
}

describe('Codex route separation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('resolves Codex to its own workspace route outside the chat view', () => {
    const router = createTestRouter()
    const codexMatch = router.resolve('/codex').matched
    const embedMatch = router.resolve('/codex/embed').matched
    const chatMatch = router.resolve('/chat').matched
    const codexComponent = codexMatch[codexMatch.length - 1]?.components?.default
    const embedComponent = embedMatch[embedMatch.length - 1]?.components?.default
    const chatComponent = chatMatch[chatMatch.length - 1]?.components?.default

    expect(router.resolve('/codex').name).toBe('codex')
    expect(router.resolve('/codex/embed').name).toBe('codex-embed')
    expect(router.resolve('/chat').name).toBe('chat')
    expect(codexMatch).toHaveLength(1)
    expect(embedMatch).toHaveLength(1)
    expect(chatMatch.length).toBeGreaterThan(1)
    expect(codexComponent).not.toBe(chatComponent)
    expect(embedComponent).not.toBe(codexComponent)
  })

  it('keeps the Codex header Chat link pointed at the chat route', async () => {
    const router = createTestRouter()
    await router.push('/codex')
    await router.isReady()

    const wrapper = mountCodexView(router)

    const chatLink = wrapper.findAll('a').find((link) => link.text().trim() === 'Chat')
    if (!chatLink) {
      throw new Error('Expected Codex header to render a Chat link.')
    }

    expect(chatLink.attributes('href')).toBe('/chat')
    expect(router.resolve(chatLink.attributes('href') ?? '').name).toBe('chat')
  })

  it('opens the mobile thread drawer and closes it after selecting a thread', async () => {
    const router = createTestRouter()
    await router.push('/codex')
    await router.isReady()
    const wrapper = mountCodexView(router)

    expect(wrapper.find('[data-codex-mobile-thread-drawer]').exists()).toBe(false)

    await wrapper.get('[data-codex-mobile-thread-drawer-open]').trigger('click')

    expect(wrapper.find('[data-codex-mobile-thread-drawer]').exists()).toBe(true)

    const sidebars = wrapper.findAllComponents({ name: 'CodexSidebar' })
    expect(sidebars).toHaveLength(2)

    const mobileSidebar = sidebars.find(
      (sidebar) => sidebar.attributes('data-codex-mobile-thread-sidebar') !== undefined,
    )

    if (!mobileSidebar) {
      throw new Error('Expected Codex mobile drawer to render a sidebar.')
    }

    await mobileSidebar.vm.$emit('select-thread', 'thread-mobile')
    await nextTick()

    expect(workspaceMock.selectThread).toHaveBeenCalledWith('thread-mobile')
    expect(wrapper.find('[data-codex-mobile-thread-drawer]').exists()).toBe(false)
  })

  it('closes the mobile thread drawer after starting a thread', async () => {
    const router = createTestRouter()
    await router.push('/codex')
    await router.isReady()
    const wrapper = mountCodexView(router)

    await wrapper.get('[data-codex-mobile-thread-drawer-open]').trigger('click')
    const mobileSidebar = wrapper
      .findAllComponents({ name: 'CodexSidebar' })
      .find((sidebar) => sidebar.attributes('data-codex-mobile-thread-sidebar') !== undefined)

    if (!mobileSidebar) {
      throw new Error('Expected Codex mobile drawer to render a sidebar.')
    }

    await mobileSidebar.vm.$emit('start-thread', '/tmp/mobile-project')
    await nextTick()

    expect(workspaceMock.startThread).toHaveBeenCalledWith('/tmp/mobile-project')
    expect(wrapper.find('[data-codex-mobile-thread-drawer]').exists()).toBe(false)
  })
})

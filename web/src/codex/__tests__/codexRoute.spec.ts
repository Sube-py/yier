import { shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import CodexView from '../../views/CodexView.vue'
import { createTestRouter } from '../../router'

vi.mock('../composables/useCodexWorkspace', () => ({
  useCodexWorkspace: () => ({
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
    selectThread: vi.fn(),
    sendPrompt: vi.fn(),
    setMode: vi.fn(),
    startThread: vi.fn(),
    status: 'idle',
    steerPrompt: vi.fn(),
    submitUserInputResponse: vi.fn(),
    successMessage: '',
    workspace: { paired_editors: [], projects: [] },
    interruptTurn: vi.fn(),
  }),
}))

describe('Codex route separation', () => {
  it('resolves Codex to its own workspace route outside the chat view', () => {
    const router = createTestRouter()
    const codexMatch = router.resolve('/codex').matched
    const chatMatch = router.resolve('/chat').matched
    const codexComponent = codexMatch[codexMatch.length - 1]?.components?.default
    const chatComponent = chatMatch[chatMatch.length - 1]?.components?.default

    expect(router.resolve('/codex').name).toBe('codex')
    expect(router.resolve('/chat').name).toBe('chat')
    expect(codexMatch).toHaveLength(1)
    expect(chatMatch.length).toBeGreaterThan(1)
    expect(codexComponent).not.toBe(chatComponent)
  })

  it('keeps the Codex header Chat link pointed at the chat route', async () => {
    const router = createTestRouter()
    await router.push('/codex')
    await router.isReady()

    const wrapper = shallowMount(CodexView, {
      global: {
        plugins: [router],
        stubs: {
          CodexComposer: true,
          CodexConversation: true,
          CodexRequestPanel: true,
          CodexSidebar: true,
          CodexThreadToolbar: true,
          RouterLink: false,
        },
      },
    })

    const chatLink = wrapper.findAll('a').find((link) => link.text().trim() === 'Chat')
    if (!chatLink) {
      throw new Error('Expected Codex header to render a Chat link.')
    }

    expect(chatLink.attributes('href')).toBe('/chat')
    expect(router.resolve(chatLink.attributes('href') ?? '').name).toBe('chat')
  })
})

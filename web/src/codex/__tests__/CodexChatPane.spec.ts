import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CodexChatPane from '../components/CodexChatPane.vue'
import type {
  CodexConversationState,
  CodexPendingRequest,
  CodexQueuedFollowup,
  CodexSocketStatus,
  CodexWorkMode,
} from '../types'

const baseProps: {
  activeThreadId: string
  activeThreadState: CodexConversationState
  activeUserInputRequest: CodexPendingRequest | null
  activeStatus: string
  activeMode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
  socketStatus: CodexSocketStatus
} = {
  activeThreadId: 'thread-1',
  activeThreadState: { id: 'thread-1', turns: [] },
  activeUserInputRequest: null,
  activeStatus: 'idle',
  activeMode: 'build',
  queuedFollowups: [],
  socketStatus: 'open',
}

function mountPane(activeUserInputRequest: CodexPendingRequest | null = null) {
  return shallowMount(CodexChatPane, {
    props: {
      ...baseProps,
      activeUserInputRequest,
    },
    global: {
      stubs: {
        CodexComposer: true,
        CodexConversation: true,
        CodexRequestPanel: true,
        CodexThreadToolbar: true,
      },
    },
  })
}

describe('CodexChatPane', () => {
  it('hides the composer while a user input request is active', () => {
    const request: CodexPendingRequest = {
      id: 'request-1',
      method: 'item/tool/requestUserInput',
      params: {
        turnId: 'turn-1',
      },
    }
    const wrapper = mountPane(request)

    expect(wrapper.findComponent({ name: 'CodexRequestPanel' }).exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'CodexComposer' }).exists()).toBe(false)
  })

  it('shows the composer when no user input request is active', () => {
    const wrapper = mountPane()

    expect(wrapper.findComponent({ name: 'CodexRequestPanel' }).exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'CodexComposer' }).exists()).toBe(true)
  })

  it('passes workspace run locations through to the composer', async () => {
    const workspace = {
      projects: [],
      paired_editors: [],
      active_remote_connection_id: 'remote-1',
      remote_connections: [
        {
          id: 'remote-1',
          display_name: 'Build host',
          ssh_host: 'user@host',
          ssh_port: 2222,
          ssh_alias: '',
          identity_file: '',
          remote_path: '~',
          auto_connect: false,
        },
      ],
    }
    const wrapper = shallowMount(CodexChatPane, {
      props: {
        ...baseProps,
        workspace,
      },
      global: {
        stubs: {
          CodexComposer: true,
          CodexConversation: true,
          CodexRequestPanel: true,
          CodexThreadToolbar: true,
        },
      },
    })

    const composer = wrapper.findComponent({ name: 'CodexComposer' })
    expect(composer.props('workspace')).toEqual(workspace)

    await composer.vm.$emit('remoteConnectionChanged')

    expect(wrapper.emitted('remoteConnectionChanged')).toHaveLength(1)
  })
})

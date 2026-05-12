import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import CodexRequestPanel from '../components/CodexRequestPanel.vue'
import type { CodexPendingRequest } from '../types'

describe('CodexRequestPanel', () => {
  it('submits structured codex-ipc question answers', async () => {
    const request: CodexPendingRequest = {
      id: 'request-1',
      method: 'item/tool/requestUserInput',
      params: {
        turnId: 'turn-1',
        questions: [
          {
            id: 'mode',
            header: 'Mode',
            question: 'How should Codex proceed?',
            options: [
              { label: 'Plan', description: 'Ask before editing.' },
              { label: 'Build', description: 'Make the change.' },
            ],
          },
        ],
      },
    }
    const wrapper = mount(CodexRequestPanel, { props: { request } })

    await wrapper.findAll('button').find((button) => button.text().includes('Plan'))!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text().includes('Submit'))!.trigger('click')

    expect(wrapper.emitted('submitResponse')).toEqual([
      [
        'request-1',
        {
          answers: {
            mode: {
              answers: ['Plan'],
            },
          },
        },
      ],
    ])
  })

  it('keeps a JSON fallback for unsupported request shapes', async () => {
    const request: CodexPendingRequest = {
      id: 'request-json',
      method: 'item/tool/requestUserInput',
      params: {
        turnId: 'turn-json',
      },
    }
    const wrapper = mount(CodexRequestPanel, { props: { request } })

    await wrapper.find('textarea').setValue(
      JSON.stringify({ answers: { custom: { answers: ['ok'] } } }),
    )
    await wrapper.findAll('button').find((button) => button.text().includes('Submit'))!.trigger('click')

    expect(wrapper.emitted('submitResponse')).toEqual([
      [
        'request-json',
        {
          answers: {
            custom: {
              answers: ['ok'],
            },
          },
        },
      ],
    ])
  })
})

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import CodexRequestPanel from '../components/CodexRequestPanel.vue'
import type { CodexPendingRequest } from '../types'

describe('CodexRequestPanel', () => {
  it('walks multiple structured questions one at a time with back navigation', async () => {
    const request: CodexPendingRequest = {
      id: 'request-many',
      method: 'item/tool/requestUserInput',
      params: {
        turnId: 'turn-many',
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
          {
            id: 'scope',
            header: 'Scope',
            question: 'How broad should the change be?',
            options: [
              { label: 'Focused', description: 'Touch the narrowest surface.' },
              { label: 'Complete', description: 'Cover adjacent cleanup too.' },
            ],
          },
        ],
      },
    }
    const wrapper = mount(CodexRequestPanel, { props: { request } })

    expect(wrapper.text()).toContain('Question 1 of 2')
    expect(wrapper.text()).toContain('Mode')
    expect(wrapper.text()).not.toContain('Scope')

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Plan'))!
      .trigger('click')

    expect(wrapper.text()).toContain('Question 2 of 2')
    expect(wrapper.text()).toContain('Scope')
    expect(wrapper.text()).not.toContain('Mode')

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Back'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Build'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Focused'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Submit'))!
      .trigger('click')

    expect(wrapper.emitted('submitResponse')).toEqual([
      [
        'request-many',
        {
          answers: {
            mode: {
              answers: ['Build'],
            },
            scope: {
              answers: ['Focused'],
            },
          },
        },
      ],
    ])
  })

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

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Plan'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Submit'))!
      .trigger('click')

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

    await wrapper
      .find('textarea')
      .setValue(JSON.stringify({ answers: { custom: { answers: ['ok'] } } }))
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('Submit'))!
      .trigger('click')

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

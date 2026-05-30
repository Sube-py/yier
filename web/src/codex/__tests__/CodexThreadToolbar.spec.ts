import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CodexThreadToolbar from '../components/CodexThreadToolbar.vue'

describe('CodexThreadToolbar', () => {
  it('keeps thread metadata visible while hiding the inline rename UI', () => {
    const wrapper = shallowMount(CodexThreadToolbar, {
      props: {
        threadId: 'thread-1234567890',
        state: {
          id: 'thread-1234567890',
          title: 'Thread title now lives in the page header',
          cwd: '/tmp/project',
          latestModel: 'gpt-5.4',
          latestReasoningEffort: 'medium',
        },
        status: 'completed',
      },
    })

    expect(wrapper.text()).toContain('project')
    expect(wrapper.text()).toContain('Completed')
    expect(wrapper.text()).toContain('gpt-5.4')
    expect(wrapper.text()).not.toContain('Thread title now lives in the page header')
    expect(wrapper.find('[data-codex-rename-form]').exists()).toBe(false)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
  })
})

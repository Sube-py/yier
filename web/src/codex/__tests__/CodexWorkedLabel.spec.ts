import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import CodexWorkedLabel from '../components/CodexWorkedLabel.vue'

describe('CodexWorkedLabel', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps ticking for working turns even when a stale duration is present', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-21T00:00:03.000Z'))

    const wrapper = mount(CodexWorkedLabel, {
      props: {
        status: 'in_progress',
        turnStartedAtMs: new Date('2026-05-21T00:00:00.000Z').getTime(),
        durationMs: 304_000,
      },
    })

    expect(wrapper.text()).toBe('Working for 3s')

    vi.advanceTimersByTime(2_000)
    await nextTick()

    expect(wrapper.text()).toBe('Working for 5s')
  })

  it('keeps ticking for working turns even when a final assistant timestamp is present', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-21T00:00:03.000Z'))

    const wrapper = mount(CodexWorkedLabel, {
      props: {
        status: 'in_progress',
        workStartedAtMs: new Date('2026-05-21T00:00:01.000Z').getTime(),
        finalAssistantStartedAtMs: new Date('2026-05-21T00:00:02.000Z').getTime(),
      },
    })

    expect(wrapper.text()).toBe('Working for 2s')

    vi.advanceTimersByTime(2_000)
    await nextTick()

    expect(wrapper.text()).toBe('Working for 4s')
  })
})

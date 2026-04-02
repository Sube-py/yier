import { describe, expect, it, vi } from 'vitest'

import { ApiError, apiGet, streamChat } from './api'
import type { ChatStreamEvent, ChatStreamRequest } from '../types/api'

describe('streamChat', () => {
  it('resolves immediately after a done event without waiting for the stream to close', async () => {
    const cancelSpy = vi.fn()
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              [
                'event: done',
                'data: {"session_id":"session-1","finish_reason":"stop"}',
                '',
                '',
              ].join('\n'),
            ),
          )
        },
        cancel() {
          cancelSpy()
        },
      })

      return new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    })
    vi.stubGlobal('fetch', fetchSpy)

    const seenEvents: ChatStreamEvent[] = []
    const payload: ChatStreamRequest = {
      session_id: 'session-1',
      message: 'hello',
    }

    const timeoutResult = Symbol('timeout')
    const result = await Promise.race([
      streamChat(payload, (event) => {
        seenEvents.push(event)
      }).then(() => 'resolved'),
      new Promise<symbol>((resolve) => {
        setTimeout(() => resolve(timeoutResult), 50)
      }),
    ])

    expect(result).toBe('resolved')
    expect(fetchSpy).toHaveBeenCalledOnce()
    expect(seenEvents).toEqual([
      {
        event: 'done',
        data: {
          session_id: 'session-1',
          finish_reason: 'stop',
        },
      },
    ])
    expect(cancelSpy).toHaveBeenCalledOnce()
  })
})

describe('api auth handling', () => {
  it('surfaces the backend auth error message on a 401 response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: 'Authentication required.' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(apiGet('/api/config')).rejects.toMatchObject({
      message: 'Authentication required.',
      status: 401,
    })
  })
})

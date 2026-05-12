import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CodexSocket, CodexSocketError } from '../lib/codexSocket'

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this)
  }

  send(payload: string) {
    this.sent.push(payload)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  open() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  receive(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }
}

describe('CodexSocket', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  it('resolves command acknowledgements and emits server events', async () => {
    const client = new CodexSocket('ws://codex.test/ws')
    const events: unknown[] = []
    client.onEvent((event) => events.push(event))

    const connectPromise = client.connect()
    const rawSocket = MockWebSocket.instances[0]!
    rawSocket.open()
    await connectPromise

    const commandPromise = client.sendCommand('list_threads', {})
    const envelope = JSON.parse(rawSocket.sent[0]!)
    rawSocket.receive({
      id: envelope.id,
      type: 'ack',
      ok: true,
      payload: { projects: [] },
    })

    await expect(commandPromise).resolves.toEqual({ projects: [] })

    rawSocket.receive({
      type: 'workspace',
      payload: { projects: [{ project: 'yier', sessions: [] }] },
    })
    expect(events).toEqual([
      {
        type: 'workspace',
        payload: { projects: [{ project: 'yier', sessions: [] }] },
      },
    ])
  })

  it('rejects a pending command when the server returns an error envelope', async () => {
    const client = new CodexSocket('ws://codex.test/ws')
    const connectPromise = client.connect()
    const rawSocket = MockWebSocket.instances[0]!
    rawSocket.open()
    await connectPromise

    const commandPromise = client.sendCommand('send_prompt', {
      thread_id: 'thread-a',
      prompt: 'hello',
    })
    const envelope = JSON.parse(rawSocket.sent[0]!)
    rawSocket.receive({
      id: envelope.id,
      type: 'error',
      code: 'bad_request',
      message: 'prompt is required.',
    })

    await expect(commandPromise).rejects.toBeInstanceOf(CodexSocketError)
    await expect(commandPromise).rejects.toMatchObject({
      code: 'bad_request',
      message: 'prompt is required.',
    })
  })
})

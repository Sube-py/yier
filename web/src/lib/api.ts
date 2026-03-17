import type { ChatStreamEvent, ChatStreamRequest } from '../types/api'

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

const JSON_HEADERS = {
  'Content-Type': 'application/json',
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path)
  return parseJsonResponse<T>(response)
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<T>(response)
}

export async function apiPut<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
  return parseJsonResponse<T>(response)
}

export async function streamChat(
  payload: ChatStreamRequest,
  onEvent: (event: ChatStreamEvent) => void,
) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })

  if (!response.ok || !response.body) {
    throw await toApiError(response)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      const event = parseEventFrame(frame)
      if (event) {
        onEvent(event)
      }
    }

    if (done) {
      break
    }
  }

  if (buffer.trim()) {
    const event = parseEventFrame(buffer)
    if (event) {
      onEvent(event)
    }
  }
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

async function toApiError(response: Response) {
  const text = await response.text()
  let message = text || `Request failed with status ${response.status}`

  try {
    const parsed = JSON.parse(text) as { detail?: string }
    if (parsed.detail) {
      message = parsed.detail
    }
  } catch {
    // Ignore invalid JSON and keep the original text body.
  }

  return new ApiError(message, response.status)
}

function parseEventFrame(frame: string): ChatStreamEvent | null {
  const lines = frame
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean)

  if (!lines.length) {
    return null
  }

  let eventName = 'message'
  const dataLines: string[] = []

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
      continue
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!dataLines.length) {
    return null
  }

  return {
    event: eventName,
    data: JSON.parse(dataLines.join('\n')),
  } as ChatStreamEvent
}

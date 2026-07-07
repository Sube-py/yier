import type { CodexConversationState, JsonRecord } from '../types'

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function compactJson(value: unknown, space = 2) {
  try {
    return JSON.stringify(value, null, space)
  } catch {
    return String(value)
  }
}

export function shortId(value: string | null | undefined, length = 8) {
  const text = value?.trim() ?? ''
  return text ? text.slice(0, length) : ''
}

export function displayPath(value: string | null | undefined) {
  const path = value?.trim() ?? ''
  if (!path) {
    return ''
  }
  const parts = path.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? path
}

export function formatTimestamp(value: number | null | undefined) {
  if (!value) {
    return ''
  }
  const milliseconds = value > 10_000_000_000 ? value : value * 1000
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - milliseconds) / 1000))
  if (deltaSeconds < 60) {
    return 'Just now'
  }
  if (deltaSeconds < 3600) {
    return `${Math.floor(deltaSeconds / 60)}m ago`
  }
  if (deltaSeconds < 86400) {
    return `${Math.floor(deltaSeconds / 3600)}h ago`
  }
  if (deltaSeconds < 604800) {
    return `${Math.floor(deltaSeconds / 86400)}d ago`
  }
  return new Date(milliseconds).toLocaleDateString()
}

export function isWorkingStatus(status: string | null | undefined) {
  return ['active', 'inprogress', 'in_progress', 'pending', 'running', 'working'].includes(
    (status ?? '').trim().toLowerCase(),
  )
}

export function statusLabel(status: string | null | undefined) {
  const normalized = (status ?? '').trim()
  if (!normalized || normalized === 'idle') {
    return 'Ready'
  }
  if (isWorkingStatus(normalized)) {
    return 'Working'
  }
  if (normalized === 'completed') {
    return 'Completed'
  }
  if (normalized === 'interrupted') {
    return 'Interrupted'
  }
  if (normalized === 'error' || normalized === 'failed') {
    return 'Error'
  }
  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

export function statusTone(status: string | null | undefined) {
  const normalized = (status ?? '').trim()
  if (isWorkingStatus(normalized)) {
    return 'text-sky-700 bg-sky-50 border-sky-200'
  }
  if (normalized === 'interrupted') {
    return 'text-amber-700 bg-amber-50 border-amber-200'
  }
  if (normalized === 'error' || normalized === 'failed') {
    return 'text-red-700 bg-red-50 border-red-200'
  }
  return 'text-emerald-700 bg-emerald-50 border-emerald-200'
}

function appendText(chunks: string[], value: unknown) {
  if (typeof value === 'string' && value) {
    chunks.push(value)
  }
}

export function textFromInput(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map(textFromInput).filter(Boolean).join('')
  }
  if (!isRecord(value)) {
    return ''
  }

  const chunks: string[] = []
  appendText(chunks, value.text)
  appendText(chunks, textFromInput(value.content))
  appendText(chunks, textFromInput(value.input))
  return chunks.join('')
}

export function activeThreadTitle(state: CodexConversationState | null) {
  const title = state?.title
  return typeof title === 'string' && title.trim() ? title.trim() : shortId(state?.id)
}

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

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

async function toApiError(response: Response) {
  redirectToLoginIfNeeded(response.status)
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

function redirectToLoginIfNeeded(status: number) {
  if (status !== 401 || typeof window === 'undefined') {
    return
  }

  if (window.location.pathname === '/login') {
    return
  }

  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`
  const searchParams = new URLSearchParams({ next })
  window.location.assign(`/login?${searchParams.toString()}`)
}

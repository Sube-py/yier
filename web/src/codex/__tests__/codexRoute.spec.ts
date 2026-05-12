import { describe, expect, it } from 'vitest'

import { createTestRouter } from '../../router'

describe('Codex route separation', () => {
  it('resolves Codex to its own workspace route outside the chat view', () => {
    const router = createTestRouter()
    const codexMatch = router.resolve('/codex').matched
    const chatMatch = router.resolve('/chat').matched
    const codexComponent = codexMatch[codexMatch.length - 1]?.components?.default
    const chatComponent = chatMatch[chatMatch.length - 1]?.components?.default

    expect(router.resolve('/codex').name).toBe('codex')
    expect(router.resolve('/chat').name).toBe('chat')
    expect(codexComponent).not.toBe(chatComponent)
  })
})

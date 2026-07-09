import { describe, expect, it } from 'vitest'

import {
  buildSlashCommands,
  clearSlashQuery,
  filterSlashCommands,
  isSlashOnlyComposerText,
  parseSlashQuery,
  scoreSlashCommand,
} from '../lib/slashCommands'

describe('slashCommands', () => {
  it('parses an active slash query at the caret', () => {
    expect(parseSlashQuery('/pl', 3)).toEqual({
      active: true,
      trigger: '/',
      query: 'pl',
      range: { start: 0, end: 3 },
    })
    expect(parseSlashQuery('hello /go', 9)).toEqual({
      active: true,
      trigger: '/',
      query: 'go',
      range: { start: 6, end: 9 },
    })
    expect(parseSlashQuery('hello /go world', 9)).toEqual({
      active: true,
      trigger: '/',
      query: 'go',
      range: { start: 6, end: 9 },
    })
    expect(parseSlashQuery('hello world', 5)).toBeNull()
    expect(parseSlashQuery('/path/to/file', 13)).toBeNull()
  })

  it('detects slash-only composer text for requiresEmptyComposer', () => {
    expect(isSlashOnlyComposerText('/compact')).toBe(true)
    expect(isSlashOnlyComposerText('  /plan  ')).toBe(true)
    expect(isSlashOnlyComposerText('keep /compact')).toBe(false)
    expect(isSlashOnlyComposerText('/path/to')).toBe(false)
  })

  it('filters and ranks slash commands like Codex autocomplete', () => {
    const commands = buildSlashCommands({
      mode: 'build',
      isWorking: false,
      hasThreadGoal: false,
      hasThread: true,
      contextPercent: 42,
      activeModel: 'gpt-5.4',
      activeReasoningEffort: 'high',
      threadId: 'thread-1',
      cwd: '/tmp/demo',
    })

    const emptyFiltered = filterSlashCommands(commands, '', { composerIsEmptyLike: true })
    expect(emptyFiltered.map((command) => command.id)).toEqual([
      'compact',
      'fork',
      'goal',
      'init',
      'model',
      'plan-mode',
      'reasoning',
      'status',
    ])

    const planMatches = filterSlashCommands(commands, 'plan', { composerIsEmptyLike: true })
    expect(planMatches[0]?.id).toBe('plan-mode')

    const nonEmpty = filterSlashCommands(commands, '', { composerIsEmptyLike: false })
    expect(nonEmpty.every((command) => !command.requiresEmptyComposer)).toBe(true)
    expect(nonEmpty.some((command) => command.id === 'compact')).toBe(false)
  })

  it('scores aliases and clears the slash query token', () => {
    const goal = buildSlashCommands({
      mode: 'build',
      isWorking: false,
      hasThreadGoal: false,
      hasThread: true,
    }).find((command) => command.id === 'goal')
    expect(goal).toBeTruthy()
    expect(scoreSlashCommand(goal!, 'go')).toBeGreaterThan(0)

    const match = parseSlashQuery('before /goal after', 12)
    expect(match).toBeTruthy()
    expect(clearSlashQuery('before /goal after', match!)).toBe('before after')
  })

  it('disables compact while a turn is working', () => {
    const commands = buildSlashCommands({
      mode: 'plan',
      isWorking: true,
      hasThreadGoal: true,
      hasThread: true,
      contextPercent: 10,
    })
    expect(commands.find((command) => command.id === 'compact')?.enabled).toBe(false)
    expect(commands.find((command) => command.id === 'goal')?.enabled).toBe(false)
    expect(commands.find((command) => command.id === 'plan-mode')?.description).toBe(
      'Turn plan mode off',
    )
  })

  it('includes skills as a grouped slash command section', () => {
    const commands = buildSlashCommands({
      mode: 'build',
      isWorking: false,
      hasThreadGoal: false,
      hasThread: true,
      skills: [
        {
          name: 'deep-research',
          display_name: 'Deep Research',
          description: 'Research thoroughly',
          short_description: 'Research thoroughly',
          path: '/skills/deep-research',
          scope: 'user',
          enabled: true,
        },
      ],
    })

    const skill = commands.find((command) => command.id === 'skill:/skills/deep-research')
    expect(skill).toMatchObject({
      title: 'Deep Research',
      group: 'Skills',
      rightLabel: 'User',
      requiresEmptyComposer: false,
      action: {
        type: 'skill',
        skill: {
          name: 'deep-research',
          path: '/skills/deep-research',
        },
      },
    })

    const filtered = filterSlashCommands(commands, 'deep', { composerIsEmptyLike: false })
    expect(filtered[0]?.id).toBe('skill:/skills/deep-research')
  })
})

import type { CodexSkillSummary } from '../types'

export type CodexSlashCommandId = string

export type CodexSlashCommandAction =
  | { type: 'compact' }
  | { type: 'fork' }
  | { type: 'goal' }
  | { type: 'init' }
  | { type: 'model' }
  | { type: 'plan-mode' }
  | { type: 'reasoning' }
  | { type: 'status' }
  | {
      type: 'skill'
      skill: {
        name: string
        path: string
        displayName?: string | null
        description?: string | null
        scope?: string | null
      }
    }

export interface CodexSlashCommandDefinition {
  id: CodexSlashCommandId
  title: string
  description: string
  /** Extra strings used for filtering, matching Codex searchAliases. */
  searchAliases?: string[]
  /** Characters that can open this command. Defaults to ['/']. */
  triggers?: string[]
  requiresEmptyComposer: boolean
  icon: string
  enabled?: boolean
  group?: string | null
  rightLabel?: string | null
  action: CodexSlashCommandAction
}

export interface CodexSlashCommandContext {
  mode: 'build' | 'plan'
  isWorking: boolean
  hasThreadGoal: boolean
  hasThread: boolean
  contextPercent?: number | null
  activeModel?: string | null
  activeReasoningEffort?: string | null
  threadId?: string | null
  cwd?: string | null
  skills?: CodexSkillSummary[]
}

export interface CodexSlashQueryMatch {
  active: boolean
  trigger: string
  query: string
  range: { start: number; end: number }
}

export const SLASH_SKILLS_GROUP = 'Skills'

const EMPTY_COMPOSER_PATTERN = /^\s*\/[^/\r\n]*\s*$/

/** Init prompt mirrored from Codex app's /init slash command. */
export const CODEX_INIT_PROMPT = `Generate a file named AGENTS.md that serves as a contributor guide for this repository.
Your goal is to produce a clear, concise, and well-structured document with descriptive headings and actionable explanations for each section.
Follow the outline below, but adapt as needed — add sections if relevant, and omit those that do not apply to this project.

Document Requirements

- Title the document "Repository Guidelines".
- Use Markdown headings (#, ##, etc.) for structure.
- Keep the document concise. 200-400 words is optimal.
- Keep explanations short, direct, and specific to this repository.
- Provide examples where helpful (commands, directory paths, naming patterns).
- Maintain a professional, instructional tone.

Recommended Sections

Project Structure & Module Organization

- Outline the project structure, including where the source code, tests, and assets are located.

Build, Test, and Development Commands

- List key commands for building, testing, and running locally (e.g., npm test, make build).
- Briefly explain what each command does.

Coding Style & Naming Conventions

- Specify indentation rules, language-specific style preferences, and naming patterns.
- Include any formatting or linting tools used.

Testing Guidelines

- Identify testing frameworks and coverage requirements.
- State test naming conventions and how to run tests.

Commit & Pull Request Guidelines

- Summarize commit message conventions found in the project’s Git history.
- Outline pull request requirements (descriptions, linked issues, screenshots).

(Optional) Security & Configuration Tips, Architecture Overview, or Agent-Specific Instructions.
`

export function isSlashOnlyComposerText(value: string): boolean {
  return EMPTY_COMPOSER_PATTERN.test(value)
}

export function parseSlashQuery(value: string, caret = value.length): CodexSlashQueryMatch | null {
  if (caret < 0 || caret > value.length) {
    return null
  }

  const beforeCaret = value.slice(0, caret)
  const lineStart = Math.max(beforeCaret.lastIndexOf('\n'), beforeCaret.lastIndexOf('\r')) + 1
  const linePrefix = beforeCaret.slice(lineStart)
  const match = /(?:^|\s)(\/)([^/\s]*)$/.exec(linePrefix)
  if (!match) {
    return null
  }

  const trigger = match[1] ?? '/'
  const query = match[2] ?? ''
  const matchStartInLine = match.index + match[0].length - trigger.length - query.length
  const start = lineStart + matchStartInLine
  const end = start + trigger.length + query.length
  return {
    active: true,
    trigger,
    query,
    range: { start, end },
  }
}

export function clearSlashQuery(value: string, match: CodexSlashQueryMatch): string {
  const before = value.slice(0, match.range.start)
  const after = value.slice(match.range.end)
  // Drop a single leading space left behind when the trigger was mid-line.
  if (before.endsWith(' ') && after.startsWith(' ')) {
    return `${before}${after.slice(1)}`
  }
  return `${before}${after}`
}

export function scoreSlashCommand(command: CodexSlashCommandDefinition, rawQuery: string): number {
  const query = rawQuery.trim().toLowerCase()
  if (!query) {
    return 1
  }

  const candidates = [
    command.id,
    command.title,
    ...(command.searchAliases ?? []),
    command.description,
  ].map((value) => value.toLowerCase())

  let best = 0
  for (const candidate of candidates) {
    if (candidate === query) {
      best = Math.max(best, 100)
      continue
    }
    if (candidate.startsWith(query)) {
      best = Math.max(best, 80 - Math.min(candidate.length - query.length, 20))
      continue
    }
    const index = candidate.indexOf(query)
    if (index >= 0) {
      best = Math.max(best, 50 - Math.min(index, 20))
    }
  }
  return best
}

export function filterSlashCommands(
  commands: CodexSlashCommandDefinition[],
  query: string,
  options: { composerIsEmptyLike: boolean },
): CodexSlashCommandDefinition[] {
  const enabled = commands.filter((command) => command.enabled !== false)
  const available = options.composerIsEmptyLike
    ? enabled
    : enabled.filter((command) => !command.requiresEmptyComposer)

  if (!query.trim()) {
    return sortSlashCommands(available)
  }

  return available
    .map((command) => ({ command, score: scoreSlashCommand(command, query) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => {
      const leftGroup = groupSortKey(left.command.group)
      const rightGroup = groupSortKey(right.command.group)
      if (leftGroup !== rightGroup) {
        return leftGroup - rightGroup
      }
      if (right.score !== left.score) {
        return right.score - left.score
      }
      return left.command.title.localeCompare(right.command.title)
    })
    .map((entry) => entry.command)
}

export function buildSlashCommands(
  context: CodexSlashCommandContext,
): CodexSlashCommandDefinition[] {
  const contextPercent =
    typeof context.contextPercent === 'number' && Number.isFinite(context.contextPercent)
      ? Math.max(0, Math.min(100, Math.round(context.contextPercent)))
      : null
  const modelLabel = context.activeModel?.trim() || 'Model'
  const reasoningLabel = formatReasoningLabel(context.activeReasoningEffort)

  return [
    {
      id: 'compact',
      title: 'Compact',
      description:
        contextPercent == null
          ? "Compact this thread's context"
          : `Compact this thread's context (${contextPercent}% full)`,
      requiresEmptyComposer: true,
      icon: 'pi pi-compress',
      enabled: context.hasThread && !context.isWorking,
      action: { type: 'compact' },
    },
    {
      id: 'fork',
      title: 'Fork',
      description: 'Fork this chat into a new local chat',
      requiresEmptyComposer: true,
      icon: 'pi pi-clone',
      enabled: context.hasThread,
      action: { type: 'fork' },
    },
    {
      id: 'goal',
      title: 'Goal',
      description: 'Set a goal that Codex will keep working towards',
      searchAliases: ['gooal', 'goaal', 'goals'],
      triggers: ['/', '@'],
      requiresEmptyComposer: false,
      icon: 'pi pi-flag',
      enabled: context.hasThread && !context.hasThreadGoal && !context.isWorking,
      action: { type: 'goal' },
    },
    {
      id: 'init',
      title: 'Init',
      description: 'Create an AGENTS.md file with instructions for Codex',
      requiresEmptyComposer: true,
      icon: 'pi pi-file-edit',
      enabled: context.hasThread && !context.isWorking,
      action: { type: 'init' },
    },
    {
      id: 'model',
      title: 'Model',
      description: modelLabel,
      requiresEmptyComposer: false,
      icon: 'pi pi-box',
      enabled: context.hasThread,
      action: { type: 'model' },
    },
    {
      id: 'plan-mode',
      title: 'Plan mode',
      description: context.mode === 'plan' ? 'Turn plan mode off' : 'Turn plan mode on',
      searchAliases: ['plan'],
      triggers: ['/', '@'],
      requiresEmptyComposer: false,
      icon: 'pi pi-list-check',
      enabled: context.hasThread,
      action: { type: 'plan-mode' },
    },
    {
      id: 'reasoning',
      title: 'Reasoning',
      description: reasoningLabel,
      requiresEmptyComposer: false,
      icon: 'pi pi-bolt',
      enabled: context.hasThread,
      action: { type: 'reasoning' },
    },
    {
      id: 'status',
      title: 'Status',
      description: 'Show chat id, context usage, and rate limits',
      requiresEmptyComposer: false,
      icon: 'pi pi-info-circle',
      enabled: context.hasThread,
      action: { type: 'status' },
    },
    ...buildSkillSlashCommands(context.skills ?? []),
  ]
}

export function buildSkillSlashCommands(
  skills: CodexSkillSummary[],
): CodexSlashCommandDefinition[] {
  return skills
    .filter((skill) => skill.enabled !== false && skill.name && skill.path)
    .map((skill) => {
      const displayName =
        (typeof skill.display_name === 'string' && skill.display_name.trim()) || skill.name
      const description =
        (typeof skill.short_description === 'string' && skill.short_description.trim()) ||
        (typeof skill.description === 'string' && skill.description.trim()) ||
        'Use this skill'
      const scopeLabel = formatSkillScope(skill.scope)
      return {
        id: `skill:${skill.path}`,
        title: displayName,
        description,
        searchAliases: [skill.name, displayName, skill.path],
        requiresEmptyComposer: false,
        icon: 'pi pi-sparkles',
        enabled: true,
        group: SLASH_SKILLS_GROUP,
        rightLabel: scopeLabel,
        action: {
          type: 'skill',
          skill: {
            name: skill.name,
            path: skill.path,
            displayName,
            description,
            scope: skill.scope ?? null,
          },
        },
      } satisfies CodexSlashCommandDefinition
    })
}

export function slashCommandToken(command: CodexSlashCommandDefinition): string {
  if (command.action.type === 'skill') {
    return command.action.skill.name
  }
  return command.id
}

function sortSlashCommands(commands: CodexSlashCommandDefinition[]) {
  return [...commands].sort((left, right) => {
    const leftGroup = groupSortKey(left.group)
    const rightGroup = groupSortKey(right.group)
    if (leftGroup !== rightGroup) {
      return leftGroup - rightGroup
    }
    if ((left.group ?? '') !== (right.group ?? '')) {
      return String(left.group ?? '').localeCompare(String(right.group ?? ''))
    }
    return left.title.localeCompare(right.title)
  })
}

function groupSortKey(group?: string | null) {
  if (!group) {
    return 0
  }
  if (group === SLASH_SKILLS_GROUP) {
    return 1
  }
  return 2
}

function formatSkillScope(scope?: string | null) {
  const normalized = scope?.trim().toLowerCase()
  if (!normalized) {
    return ''
  }
  if (normalized === 'repo') {
    return 'Repo'
  }
  if (normalized === 'user') {
    return 'User'
  }
  if (normalized === 'system') {
    return 'System'
  }
  if (normalized === 'admin') {
    return 'Admin'
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatReasoningLabel(value?: string | null): string {
  const normalized = value?.trim().toLowerCase()
  if (!normalized) {
    return 'Reasoning effort'
  }
  if (normalized === 'xhigh') {
    return 'Extra high'
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

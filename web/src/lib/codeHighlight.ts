import hljs from 'highlight.js/lib/common'

export const COMMON_LANGUAGE_ALIASES: Record<string, string> = {
  atom: 'xml',
  bat: 'bash',
  'bash-session': 'bash',
  'c#': 'csharp',
  'c++': 'cpp',
  cc: 'cpp',
  cfg: 'ini',
  cjs: 'javascript',
  conf: 'ini',
  config: 'ini',
  console: 'bash',
  cts: 'typescript',
  cs: 'csharp',
  csharp: 'csharp',
  cxx: 'cpp',
  diff: 'diff',
  docker: 'bash',
  dockerfile: 'bash',
  dotenv: 'ini',
  env: 'ini',
  gitdiff: 'diff',
  gql: 'graphql',
  htm: 'xml',
  html: 'xml',
  ini: 'ini',
  javscript: 'javascript',
  js: 'javascript',
  json5: 'json',
  jsonc: 'json',
  jsx: 'javascript',
  kt: 'kotlin',
  kts: 'kotlin',
  log: 'plaintext',
  make: 'makefile',
  md: 'markdown',
  mdown: 'markdown',
  mdx: 'markdown',
  mjs: 'javascript',
  mts: 'typescript',
  'objective-c': 'objectivec',
  objc: 'objectivec',
  patch: 'diff',
  plain: 'plaintext',
  plaintext: 'plaintext',
  powershell: 'bash',
  properties: 'ini',
  ps: 'bash',
  ps1: 'bash',
  psm1: 'bash',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  rss: 'xml',
  sh: 'bash',
  shell: 'bash',
  'shell-session': 'bash',
  shellscript: 'bash',
  svg: 'xml',
  terminal: 'bash',
  text: 'plaintext',
  'text-only': 'plaintext',
  'text/plain': 'plaintext',
  textfile: 'plaintext',
  toml: 'ini',
  ts: 'typescript',
  tsx: 'typescript',
  txt: 'plaintext',
  vim: 'plaintext',
  vue: 'xml',
  wsdl: 'xml',
  xhtml: 'xml',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
  zsh: 'bash',
  'zsh-session': 'bash',
}

export function normalizeHighlightLanguage(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/^language-/, '')
    .replace(/^lang-/, '')
}

export function resolveHighlightLanguage(value: string) {
  const normalizedLanguage = normalizeHighlightLanguage(value)
  const aliasLanguage = COMMON_LANGUAGE_ALIASES[normalizedLanguage] ?? normalizedLanguage
  if (aliasLanguage && hljs.getLanguage(aliasLanguage)) {
    return {
      requestedLanguage: normalizedLanguage,
      highlightLanguage: aliasLanguage,
    }
  }
  if (normalizedLanguage && hljs.getLanguage(normalizedLanguage)) {
    return {
      requestedLanguage: normalizedLanguage,
      highlightLanguage: normalizedLanguage,
    }
  }
  return {
    requestedLanguage: normalizedLanguage,
    highlightLanguage: '',
  }
}

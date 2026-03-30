<script setup lang="ts">
import { computed, ref, toRefs } from 'vue'

import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import ScrollPanel from 'primevue/scrollpanel'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Tabs from 'primevue/tabs'
import Tab from 'primevue/tab'
import TabList from 'primevue/tablist'
import TabPanel from 'primevue/tabpanel'
import TabPanels from 'primevue/tabpanels'
import Textarea from 'primevue/textarea'

import type {
  BackendId,
  ConfigResponse,
  EditableAllowedRoot,
  EditableMcpServer,
  HealthResponse,
  McpConfigResponse,
} from '../types/api'

const props = defineProps<{
  health: HealthResponse | null
  config: ConfigResponse | null
  mcpConfig: McpConfigResponse | null
  backendOptions: Array<{ id: BackendId; label: string }>
  llmForm: {
    provider: '' | 'zai' | 'zai-coding-plan'
    baseUrl: string
    model: string
    apiKey: string
  }
  appForm: {
    defaultBackendId: BackendId
    defaultProjectPath: string
    channelBackendId: BackendId
    channelProjectPath: string
    channelAutoApproveCodexRequests: boolean
    codexLauncherCommand: string
    codexModel: string
    codexSandbox: 'read-only' | 'workspace-write' | 'danger-full-access'
    codexApprovalPolicy: 'untrusted' | 'on-failure' | 'on-request' | 'never'
    codexApprovalsReviewer: 'user' | 'guardian_subagent'
    codexPersonality: 'none' | 'friendly' | 'pragmatic'
    codexReasoningEffort: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh'
    codexShowReasoningCards: boolean
    codexServiceTier: '' | 'fast' | 'flex'
  }
  rootsDraft: EditableAllowedRoot[]
  mcpDraft: EditableMcpServer[]
  savingApp: boolean
  savingLlm: boolean
  savingRoots: boolean
  savingMcp: boolean
  reloadingMcp: boolean
}>()

const {
  config,
  health,
  llmForm,
  mcpConfig,
  mcpDraft,
  backendOptions,
  appForm,
  reloadingMcp,
  rootsDraft,
  savingApp,
  savingLlm,
  savingMcp,
  savingRoots,
} = toRefs(props)

const activeTab = ref('llm')
const isPresetProvider = computed(() => llmForm.value.provider !== '')
const providerOptions = [
  { label: 'Custom', value: '' },
  { label: 'Z.AI', value: 'zai' },
  { label: 'Z.AI Coding Plan', value: 'zai-coding-plan' },
]

const emit = defineEmits<{
  saveLlm: []
  saveApp: []
  saveRoots: []
  resetRoots: []
  addRoot: []
  removeRoot: [rootId: string]
  saveMcp: []
  reloadMcp: []
  addMcp: []
  removeMcp: [serverId: string]
}>()
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col overflow-hidden">
    <ScrollPanel class="min-h-0 flex-1">
      <div class="pr-[0.35rem]">
        <div class="flex items-start justify-between gap-4 border-b border-[color:var(--app-border)] pb-4 max-md:flex-col max-md:items-stretch">
          <div>
            <p class="eyebrow">Configuration</p>
            <h3>Local console settings</h3>
            <p class="mt-[0.65rem] mb-0 max-w-[42rem] leading-[1.6] text-[color:var(--app-text-soft)]">
              Tune the local model connection, maintain MCP servers, and inspect runtime status
              without leaving the main workspace.
            </p>
          </div>
          <Tag
            :value="health?.llm.ready ? 'Configured' : 'Needs setup'"
            :severity="health?.llm.ready ? 'success' : 'warn'"
          />
        </div>

        <Tabs v-model:value="activeTab" class="mt-4">
          <TabList>
            <Tab value="llm">LLM</Tab>
            <Tab value="backends">Backends</Tab>
            <Tab value="workspace">Workspace</Tab>
            <Tab value="mcp">MCP</Tab>
            <Tab value="runtime">Runtime</Tab>
          </TabList>
          <TabPanels>
            <TabPanel value="llm">
              <section class="grid gap-[0.9rem] pt-4">
                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="provider">Provider</label>
                <Select
                  input-id="provider"
                  v-model="llmForm.provider"
                  :options="providerOptions"
                  option-label="label"
                  option-value="value"
                  placeholder="Select a provider"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="base-url">
                  {{ isPresetProvider ? 'Base URL Override' : 'Base URL' }}
                </label>
                <InputText
                  id="base-url"
                  v-model="llmForm.baseUrl"
                  fluid
                  :placeholder="
                    isPresetProvider
                      ? 'Optional override for the preset endpoint'
                      : 'https://api.example.com/v1'
                  "
                />
                <p class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
                  {{
                    isPresetProvider
                      ? 'Preset providers prefill the official endpoint and model. Edit them only if you need an override.'
                      : 'Required for custom OpenAI-compatible providers.'
                  }}
                </p>

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="model">Model</label>
                <InputText id="model" v-model="llmForm.model" fluid placeholder="gpt-4.1-mini" />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="api-key">API Key</label>
                <Password
                  id="api-key"
                  v-model="llmForm.apiKey"
                  fluid
                  toggle-mask
                  :feedback="false"
                  placeholder="Leave blank to keep the current key"
                />

                <Button
                  label="Save LLM Settings"
                  icon="pi pi-check"
                  :loading="savingLlm"
                  @click="emit('saveLlm')"
                />
              </section>
            </TabPanel>

            <TabPanel value="backends">
              <section class="grid gap-[0.9rem] pt-4">
                <p class="eyebrow">Session defaults</p>
                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="default-backend">New chat backend</label>
                <Select
                  input-id="default-backend"
                  v-model="appForm.defaultBackendId"
                  :options="backendOptions"
                  option-label="label"
                  option-value="id"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="default-project-path">New chat project path</label>
                <InputText
                  id="default-project-path"
                  v-model="appForm.defaultProjectPath"
                  fluid
                  placeholder="project/root or ./relative/path"
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="channel-backend">Channel backend</label>
                <Select
                  input-id="channel-backend"
                  v-model="appForm.channelBackendId"
                  :options="backendOptions"
                  option-label="label"
                  option-value="id"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="channel-project-path">Channel project path</label>
                <InputText
                  id="channel-project-path"
                  v-model="appForm.channelProjectPath"
                  fluid
                  placeholder="channel/root or ./relative/path"
                />

                <label class="inline-flex items-center gap-1.5 text-[0.92rem] text-[color:var(--app-text-soft)]">
                  <input v-model="appForm.channelAutoApproveCodexRequests" type="checkbox" />
                  Auto-resolve Codex approvals in channel sessions
                </label>

                <p class="eyebrow">Codex app-server</p>
                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-launcher-command">Launcher command</label>
                <InputText
                  id="codex-launcher-command"
                  v-model="appForm.codexLauncherCommand"
                  fluid
                  placeholder="codex app-server --listen stdio://"
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-model">Default model</label>
                <InputText
                  id="codex-model"
                  v-model="appForm.codexModel"
                  fluid
                  placeholder="gpt-5.4"
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-sandbox">Sandbox</label>
                <Select
                  input-id="codex-sandbox"
                  v-model="appForm.codexSandbox"
                  :options="[
                    { label: 'Read only', value: 'read-only' },
                    { label: 'Workspace write', value: 'workspace-write' },
                    { label: 'Danger full access', value: 'danger-full-access' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-approval-policy">Approval policy</label>
                <Select
                  input-id="codex-approval-policy"
                  v-model="appForm.codexApprovalPolicy"
                  :options="[
                    { label: 'Untrusted', value: 'untrusted' },
                    { label: 'On failure', value: 'on-failure' },
                    { label: 'On request', value: 'on-request' },
                    { label: 'Never', value: 'never' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-reviewer">Approvals reviewer</label>
                <Select
                  input-id="codex-reviewer"
                  v-model="appForm.codexApprovalsReviewer"
                  :options="[
                    { label: 'User', value: 'user' },
                    { label: 'Guardian subagent', value: 'guardian_subagent' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-personality">Personality</label>
                <Select
                  input-id="codex-personality"
                  v-model="appForm.codexPersonality"
                  :options="[
                    { label: 'None', value: 'none' },
                    { label: 'Friendly', value: 'friendly' },
                    { label: 'Pragmatic', value: 'pragmatic' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-effort">Reasoning effort</label>
                <Select
                  input-id="codex-effort"
                  v-model="appForm.codexReasoningEffort"
                  :options="[
                    { label: 'None', value: 'none' },
                    { label: 'Minimal', value: 'minimal' },
                    { label: 'Low', value: 'low' },
                    { label: 'Medium', value: 'medium' },
                    { label: 'High', value: 'high' },
                    { label: 'Extra high', value: 'xhigh' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]" for="codex-service-tier">Service tier</label>
                <Select
                  input-id="codex-service-tier"
                  v-model="appForm.codexServiceTier"
                  :options="[
                    { label: 'Default', value: '' },
                    { label: 'Fast', value: 'fast' },
                    { label: 'Flex', value: 'flex' },
                  ]"
                  option-label="label"
                  option-value="value"
                  fluid
                />

                <label class="inline-flex items-center gap-1.5 text-[0.92rem] text-[color:var(--app-text-soft)]">
                  <input v-model="appForm.codexShowReasoningCards" type="checkbox" />
                  Show reasoning activity cards in chat
                </label>

                <Button
                  label="Save Backend Settings"
                  icon="pi pi-check"
                  :loading="savingApp"
                  @click="emit('saveApp')"
                />
              </section>
            </TabPanel>

            <TabPanel value="workspace">
              <section class="grid gap-[0.9rem] pt-4">
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <p class="eyebrow">Allowed roots</p>
                    <h4>Choose which directories chat tools can access.</h4>
                  </div>
                  <Button
                    label="Add Directory"
                    icon="pi pi-plus"
                    severity="secondary"
                    outlined
                    @click="emit('addRoot')"
                  />
                </div>

                <article
                  v-for="root in rootsDraft"
                  :key="root.id"
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-[0.65rem] rounded-[1.05rem] border border-[color:var(--app-border)] bg-[rgba(255,250,242,0.82)] px-[0.9rem] py-[0.85rem] max-md:grid-cols-1"
                >
                  <InputText
                    v-model="root.path"
                    fluid
                    placeholder="~/Documents or /absolute/path"
                  />
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    @click="emit('removeRoot', root.id)"
                  />
                </article>

                <p class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
                  Relative paths are resolved from the project root. `~` expands to the current home
                  directory.
                </p>

                <div class="flex justify-end gap-3">
                  <Button
                    label="Save Directories"
                    icon="pi pi-folder-open"
                    :loading="savingRoots"
                    @click="emit('saveRoots')"
                  />
                  <Button
                    label="Restore Defaults"
                    icon="pi pi-refresh"
                    severity="secondary"
                    outlined
                    @click="emit('resetRoots')"
                  />
                </div>
              </section>
            </TabPanel>

            <TabPanel value="mcp">
              <section class="grid gap-[0.9rem] pt-4">
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <p class="eyebrow">MCP servers</p>
                    <h4>Edit the same `~/.yier/.yier.json` registry.</h4>
                  </div>
                  <Button
                    label="Add Server"
                    icon="pi pi-plus"
                    severity="secondary"
                    outlined
                    @click="emit('addMcp')"
                  />
                </div>

                <article
                  v-for="server in mcpDraft"
                  :key="server.id"
                  class="grid gap-3 rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
                >
                  <div class="flex items-center justify-between gap-3 max-md:flex-col max-md:items-stretch">
                    <InputText v-model="server.name" fluid placeholder="server-name" />
                    <select
                      v-model="server.type"
                      class="min-w-28 rounded-[0.9rem] border border-[color:var(--app-border)] bg-[rgba(255,252,245,0.9)] px-[0.8rem] py-[0.7rem]"
                    >
                      <option value="stdio">stdio</option>
                      <option value="http">http</option>
                      <option value="sse">sse</option>
                    </select>
                    <label class="inline-flex items-center gap-1.5 text-[0.92rem] text-[color:var(--app-text-soft)]">
                      <input v-model="server.enabled" type="checkbox" />
                      Enabled
                    </label>
                    <Button
                      icon="pi pi-trash"
                      text
                      severity="danger"
                      @click="emit('removeMcp', server.id)"
                    />
                  </div>

                  <InputText
                    v-if="server.type === 'stdio'"
                    v-model="server.command"
                    fluid
                    placeholder="command"
                  />
                  <InputText
                    v-else
                    v-model="server.url"
                    fluid
                    placeholder="https://server.example.com/mcp"
                  />

                  <InputText v-model="server.status" fluid placeholder="optional status hint" />

                  <label
                    v-if="server.type === 'stdio'"
                    class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]"
                  >Args JSON</label>
                  <Textarea
                    v-if="server.type === 'stdio'"
                    v-model="server.argsText"
                    auto-resize
                    fluid
                    rows="3"
                    placeholder='["mcp-server-fetch"]'
                  />

                  <label class="text-[0.88rem] font-bold text-[color:var(--app-text-soft)]">{{
                    server.type === 'stdio' ? 'Env JSON' : 'Headers JSON'
                  }}</label>
                  <Textarea
                    v-if="server.type === 'stdio'"
                    v-model="server.envText"
                    auto-resize
                    fluid
                    rows="4"
                    placeholder='{"KEY":"value"}'
                  />
                  <Textarea
                    v-else
                    v-model="server.headersText"
                    auto-resize
                    fluid
                    rows="4"
                    placeholder='{"Authorization":"Bearer token"}'
                  />
                </article>

                <div class="flex justify-end gap-3">
                  <Button
                    label="Save MCP"
                    icon="pi pi-save"
                    :loading="savingMcp"
                    @click="emit('saveMcp')"
                  />
                  <Button
                    label="Reload Runtime"
                    icon="pi pi-refresh"
                    severity="secondary"
                    outlined
                    :loading="reloadingMcp"
                    @click="emit('reloadMcp')"
                  />
                </div>
              </section>
            </TabPanel>

            <TabPanel value="runtime">
              <section class="grid grid-cols-2 gap-[0.9rem] pt-4 max-md:grid-cols-1">
                <article
                  class="rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
                >
                  <p class="eyebrow">Frontend</p>
                  <h4>{{ health?.frontend.mode ?? 'unknown' }}</h4>
                  <p>{{ health?.frontend.detail ?? 'No frontend details yet.' }}</p>
                </article>
                <article
                  class="rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
                >
                  <p class="eyebrow">LLM</p>
                  <h4>{{ health?.llm.ready ? 'Ready' : 'Needs setup' }}</h4>
                  <p>{{ health?.llm.detail ?? 'Saved in ~/.yier/web/settings.json.' }}</p>
                </article>
                <article
                  class="rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
                >
                  <p class="eyebrow">Allowed Roots</p>
                  <ul class="mt-3 ml-4 p-0 leading-[1.5] text-[color:var(--app-text-soft)]">
                    <li v-for="root in config?.allowed_roots ?? []" :key="root">{{ root }}</li>
                  </ul>
                </article>
                <article
                  class="col-span-full rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px] max-md:col-auto"
                >
                  <p class="eyebrow">MCP Runtime</p>
                  <ul class="mt-3 ml-4 p-0 leading-[1.5] text-[color:var(--app-text-soft)]">
                    <li v-for="(entry, name) in mcpConfig?.runtime ?? {}" :key="name">
                      <strong>{{ name }}</strong>
                      <span class="block text-[color:var(--app-text-soft)]">
                        {{ entry.status }} · {{ entry.tool_count }} tools
                      </span>
                      <span v-if="entry.error" class="block text-[color:var(--app-text-soft)]">
                        {{ entry.error }}
                      </span>
                    </li>
                  </ul>
                </article>
              </section>
            </TabPanel>
          </TabPanels>
        </Tabs>
      </div>
    </ScrollPanel>
  </section>
</template>

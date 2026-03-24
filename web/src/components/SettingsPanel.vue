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
  <section class="settings-page">
    <ScrollPanel class="settings-scrollpanel">
      <div class="settings-scrollpanel-content">
        <div class="settings-page-header">
          <div>
            <p class="eyebrow">Configuration</p>
            <h3>Local console settings</h3>
            <p class="settings-page-copy">
              Tune the local model connection, maintain MCP servers, and inspect runtime status
              without leaving the main workspace.
            </p>
          </div>
          <Tag
            :value="health?.llm.ready ? 'Configured' : 'Needs setup'"
            :severity="health?.llm.ready ? 'success' : 'warn'"
          />
        </div>

        <Tabs v-model:value="activeTab" class="settings-tabs">
          <TabList>
            <Tab value="llm">LLM</Tab>
            <Tab value="backends">Backends</Tab>
            <Tab value="workspace">Workspace</Tab>
            <Tab value="mcp">MCP</Tab>
            <Tab value="runtime">Runtime</Tab>
          </TabList>
          <TabPanels>
            <TabPanel value="llm">
              <section class="settings-section">
                <label class="field-label" for="provider">Provider</label>
                <Select
                  input-id="provider"
                  v-model="llmForm.provider"
                  :options="providerOptions"
                  option-label="label"
                  option-value="value"
                  placeholder="Select a provider"
                  fluid
                />

                <label class="field-label" for="base-url">
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
                <p class="settings-hint">
                  {{
                    isPresetProvider
                      ? 'Preset providers prefill the official endpoint and model. Edit them only if you need an override.'
                      : 'Required for custom OpenAI-compatible providers.'
                  }}
                </p>

                <label class="field-label" for="model">Model</label>
                <InputText id="model" v-model="llmForm.model" fluid placeholder="gpt-4.1-mini" />

                <label class="field-label" for="api-key">API Key</label>
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
              <section class="settings-section">
                <p class="eyebrow">Session defaults</p>
                <label class="field-label" for="default-backend">New chat backend</label>
                <Select
                  input-id="default-backend"
                  v-model="appForm.defaultBackendId"
                  :options="backendOptions"
                  option-label="label"
                  option-value="id"
                  fluid
                />

                <label class="field-label" for="default-project-path">New chat project path</label>
                <InputText
                  id="default-project-path"
                  v-model="appForm.defaultProjectPath"
                  fluid
                  placeholder="project/root or ./relative/path"
                />

                <label class="field-label" for="channel-backend">Channel backend</label>
                <Select
                  input-id="channel-backend"
                  v-model="appForm.channelBackendId"
                  :options="backendOptions"
                  option-label="label"
                  option-value="id"
                  fluid
                />

                <label class="field-label" for="channel-project-path">Channel project path</label>
                <InputText
                  id="channel-project-path"
                  v-model="appForm.channelProjectPath"
                  fluid
                  placeholder="channel/root or ./relative/path"
                />

                <label class="mcp-toggle">
                  <input v-model="appForm.channelAutoApproveCodexRequests" type="checkbox" />
                  Auto-resolve Codex approvals in channel sessions
                </label>

                <p class="eyebrow">Codex app-server</p>
                <label class="field-label" for="codex-launcher-command">Launcher command</label>
                <InputText
                  id="codex-launcher-command"
                  v-model="appForm.codexLauncherCommand"
                  fluid
                  placeholder="codex app-server --listen stdio://"
                />

                <label class="field-label" for="codex-model">Default model</label>
                <InputText
                  id="codex-model"
                  v-model="appForm.codexModel"
                  fluid
                  placeholder="gpt-5.4"
                />

                <label class="field-label" for="codex-sandbox">Sandbox</label>
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

                <label class="field-label" for="codex-approval-policy">Approval policy</label>
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

                <label class="field-label" for="codex-reviewer">Approvals reviewer</label>
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

                <label class="field-label" for="codex-personality">Personality</label>
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

                <label class="field-label" for="codex-effort">Reasoning effort</label>
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

                <label class="field-label" for="codex-service-tier">Service tier</label>
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

                <Button
                  label="Save Backend Settings"
                  icon="pi pi-check"
                  :loading="savingApp"
                  @click="emit('saveApp')"
                />
              </section>
            </TabPanel>

            <TabPanel value="workspace">
              <section class="settings-section">
                <div class="section-header-row">
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

                <article v-for="root in rootsDraft" :key="root.id" class="root-editor-card">
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

                <p class="settings-hint">
                  Relative paths are resolved from the project root. `~` expands to the current home
                  directory.
                </p>

                <div class="settings-actions">
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
              <section class="settings-section">
                <div class="section-header-row">
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

                <article v-for="server in mcpDraft" :key="server.id" class="mcp-card">
                  <div class="mcp-card-header">
                    <InputText v-model="server.name" fluid placeholder="server-name" />
                    <select v-model="server.type" class="mcp-select">
                      <option value="stdio">stdio</option>
                      <option value="http">http</option>
                      <option value="sse">sse</option>
                    </select>
                    <label class="mcp-toggle">
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

                  <label v-if="server.type === 'stdio'" class="field-label">Args JSON</label>
                  <Textarea
                    v-if="server.type === 'stdio'"
                    v-model="server.argsText"
                    auto-resize
                    fluid
                    rows="3"
                    placeholder='["mcp-server-fetch"]'
                  />

                  <label class="field-label">{{
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

                <div class="settings-actions">
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
              <section class="settings-section runtime-grid">
                <article class="runtime-card">
                  <p class="eyebrow">Frontend</p>
                  <h4>{{ health?.frontend.mode ?? 'unknown' }}</h4>
                  <p>{{ health?.frontend.detail ?? 'No frontend details yet.' }}</p>
                </article>
                <article class="runtime-card">
                  <p class="eyebrow">LLM</p>
                  <h4>{{ health?.llm.ready ? 'Ready' : 'Needs setup' }}</h4>
                  <p>{{ health?.llm.detail ?? 'Saved in ~/.yier/web/settings.json.' }}</p>
                </article>
                <article class="runtime-card">
                  <p class="eyebrow">Allowed Roots</p>
                  <ul class="runtime-list">
                    <li v-for="root in config?.allowed_roots ?? []" :key="root">{{ root }}</li>
                  </ul>
                </article>
                <article class="runtime-card runtime-card--wide">
                  <p class="eyebrow">MCP Runtime</p>
                  <ul class="runtime-list">
                    <li v-for="(entry, name) in mcpConfig?.runtime ?? {}" :key="name">
                      <strong>{{ name }}</strong>
                      <span>{{ entry.status }} · {{ entry.tool_count }} tools</span>
                      <span v-if="entry.error">{{ entry.error }}</span>
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

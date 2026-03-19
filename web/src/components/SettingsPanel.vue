<script setup lang="ts">
import { ref } from 'vue'

import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Tag from 'primevue/tag'
import Tabs from 'primevue/tabs'
import Tab from 'primevue/tab'
import TabList from 'primevue/tablist'
import TabPanel from 'primevue/tabpanel'
import TabPanels from 'primevue/tabpanels'
import Textarea from 'primevue/textarea'

import type { ConfigResponse, EditableMcpServer, HealthResponse, McpConfigResponse } from '../types/api'

defineProps<{
  health: HealthResponse | null
  config: ConfigResponse | null
  mcpConfig: McpConfigResponse | null
  llmForm: {
    baseUrl: string
    model: string
    apiKey: string
  }
  mcpDraft: EditableMcpServer[]
  savingLlm: boolean
  savingMcp: boolean
  reloadingMcp: boolean
}>()

const activeTab = ref('llm')

const emit = defineEmits<{
  saveLlm: []
  saveMcp: []
  reloadMcp: []
  addMcp: []
  removeMcp: [serverId: string]
}>()
</script>

<template>
  <section class="settings-page">
    <div class="settings-page-header">
      <div>
        <p class="eyebrow">Configuration</p>
        <h3>Local console settings</h3>
        <p class="settings-page-copy">
          Tune the local model connection, maintain MCP servers, and inspect runtime status without
          leaving the main workspace.
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
        <Tab value="mcp">MCP</Tab>
        <Tab value="runtime">Runtime</Tab>
      </TabList>
      <TabPanels>
        <TabPanel value="llm">
          <section class="settings-section">
            <label class="field-label" for="base-url">Base URL</label>
            <InputText id="base-url" v-model="llmForm.baseUrl" fluid placeholder="https://api.example.com/v1" />

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
                <Button icon="pi pi-trash" text severity="danger" @click="emit('removeMcp', server.id)" />
              </div>

              <InputText v-if="server.type === 'stdio'" v-model="server.command" fluid placeholder="command" />
              <InputText v-else v-model="server.url" fluid placeholder="https://server.example.com/mcp" />

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

              <label class="field-label">{{ server.type === 'stdio' ? 'Env JSON' : 'Headers JSON' }}</label>
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
  </section>
</template>

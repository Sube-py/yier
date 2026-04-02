<script setup lang="ts">
import SettingsPanel from '../components/SettingsPanel.vue'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <SettingsPanel
    :health="workspace.health"
    :config="workspace.config"
    :mcp-config="workspace.mcpConfig"
    :channel-workspace="workspace.channelWorkspace"
    :channel-platforms="workspace.channelPlatforms"
    :channel-config="workspace.channelConfig"
    :channel-monitor-sessions="workspace.channelMonitorSessions"
    :channel-login-state="workspace.channelLoginState"
    :backend-options="workspace.backendOptions"
    :llm-form="workspace.llmForm"
    :app-form="workspace.appForm"
    :roots-draft="workspace.rootsDraft"
    :mcp-draft="workspace.mcpDraft"
    :saving-app="workspace.savingState.app"
    :saving-llm="workspace.savingState.llm"
    :saving-roots="workspace.savingState.roots"
    :saving-mcp="workspace.savingState.mcp"
    :reloading-mcp="workspace.savingState.reloadingMcp"
    @login-weixin="workspace.loginWeixin"
    @start-account="workspace.startChannelAccount"
    @stop-account="workspace.stopChannelAccount"
    @open-channel-session="workspace.openSessionFromHistory"
    @save-llm="workspace.saveLlmSettings"
    @save-app="workspace.saveAppSettings"
    @save-roots="workspace.saveAllowedRoots"
    @reset-roots="workspace.resetAllowedRoots"
    @add-root="workspace.addAllowedRoot"
    @remove-root="workspace.removeAllowedRoot"
    @save-mcp="workspace.saveMcpSettings"
    @reload-mcp="workspace.reloadMcpSettings"
    @add-mcp="workspace.addMcpServer"
    @remove-mcp="workspace.removeMcpServer"
  />
</template>

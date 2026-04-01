<script setup lang="ts">
import ChannelWorkspacePanel from '../components/ChannelWorkspacePanel.vue'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <ChannelWorkspacePanel
    :workspace="workspace.channelWorkspace"
    :platforms="workspace.channelPlatforms"
    :config="workspace.channelConfig"
    :monitor-sessions="workspace.channelMonitorSessions"
    :login-state="workspace.channelLoginState"
    @login-weixin="workspace.loginWeixin"
    @start-account="workspace.startChannelAccount"
    @stop-account="workspace.stopChannelAccount"
    @open-session="workspace.openSessionFromHistory"
  />
</template>

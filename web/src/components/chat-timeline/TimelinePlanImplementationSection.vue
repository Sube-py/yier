<script setup lang="ts">
import { ref } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'

import type { ChatActivity } from '../../types/api'

const props = defineProps<{
  activity: ChatActivity
}>()

const emit = defineEmits<{
  implementPlan: [{ planContent: string; userInput: string | null }]
}>()

const userInput = ref('')

function onImplement() {
  const text = userInput.value.trim()
  emit('implementPlan', {
    planContent: props.activity.planImplementation?.planContent || '',
    userInput: text || null,
  })
}

function onCancel() {
  if (props.activity.planImplementation) {
    props.activity.planImplementation.submittedAt = -1
  }
}
</script>

<template>
  <div class="plan-impl-card grid gap-[0.7rem]">
    <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">
      Plan generated. Implement it or provide feedback below.
    </p>

    <Textarea
      v-model="userInput"
      auto-resize
      fluid
      rows="2"
      placeholder="Optional: suggest changes to the plan..."
    />

    <div class="flex flex-wrap gap-2">
      <Button
        label="Implement Plan"
        size="small"
        @click="onImplement"
      />
      <Button
        label="Cancel"
        size="small"
        severity="secondary"
        outlined
        @click="onCancel"
      />
    </div>
  </div>
</template>

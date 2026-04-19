<script setup lang="ts">
import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Textarea from 'primevue/textarea'

import type { ApprovalDecision, ChatActivity } from '../../types/api'
import {
  approvalFieldPrompt,
  approvalFieldValue,
  approvalHasUrl,
  approvalMessage,
  approvalSchemaPreview,
  approvalSubmitPayload,
  approvalUrl,
  approvalUsesJsonFallback,
  approvalUsesStructuredForm,
  clearApprovalValidation,
  updateApprovalFieldValue,
  updateApprovalMultiSelect,
} from './helpers'

const props = defineProps<{
  activity: ChatActivity
}>()

const emit = defineEmits<{
  submitApproval: [
    { requestId: string | number; decision: ApprovalDecision; contentText: string },
  ]
}>()

function onApprovalInput(field: NonNullable<ChatActivity['approval']>['formFields'][number], event: Event) {
  const target = event.target
  if (!(target instanceof HTMLInputElement)) {
    return
  }
  updateApprovalFieldValue(field, target.value)
  clearApprovalValidation(props.activity)
}

function onApprovalSelect(field: NonNullable<ChatActivity['approval']>['formFields'][number], event: Event) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  updateApprovalFieldValue(field, target.value)
  clearApprovalValidation(props.activity)
}

function onApprovalMultiSelect(field: NonNullable<ChatActivity['approval']>['formFields'][number], event: Event) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  updateApprovalMultiSelect(field, target)
  clearApprovalValidation(props.activity)
}

function submitApproval(decision: ApprovalDecision) {
  const payload = approvalSubmitPayload(props.activity, decision)
  if (!payload) {
    return
  }
  emit('submitApproval', payload)
}
</script>

<template>
  <div class="approval-card grid gap-[0.7rem]">
    <p
      v-if="approvalMessage(activity)"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      {{ approvalMessage(activity) }}
    </p>

    <p
      v-if="approvalHasUrl(activity)"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      Open
      <a
        :href="approvalUrl(activity)"
        target="_blank"
        rel="noreferrer"
      >
        {{ approvalUrl(activity) }}
      </a>
    </p>

    <div
      v-if="approvalSchemaPreview(activity)"
      class="grid gap-[0.3rem]"
    >
      <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">Requested schema</p>
      <ScrollPanel class="w-full">
        <pre
          class="rounded-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]"
        >{{ approvalSchemaPreview(activity) }}</pre>
      </ScrollPanel>
    </div>

    <div
      v-if="approvalUsesStructuredForm(activity) && activity.approval"
      class="grid gap-[0.7rem]"
    >
      <label
        v-for="field in activity.approval.formFields"
        :key="`${activity.id}-${field.id}`"
        class="grid gap-1"
      >
        <span class="text-[0.92rem] font-bold text-[color:var(--app-text)]">
          {{ field.label }}
          <span
            v-if="field.required"
            class="text-[#bc5f38]"
          >*</span>
        </span>
        <span
          v-if="approvalFieldPrompt(field)"
          class="text-[0.82rem] leading-[1.5] text-[color:var(--app-text-soft)]"
        >
          {{ approvalFieldPrompt(field) }}
        </span>

        <input
          v-if="field.kind === 'text'"
          class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          type="text"
          :value="approvalFieldValue(field)"
          @input="onApprovalInput(field, $event)"
        />
        <input
          v-else-if="field.kind === 'number'"
          class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          :step="field.integer ? 1 : 'any'"
          :min="field.min ?? undefined"
          :max="field.max ?? undefined"
          type="number"
          :value="approvalFieldValue(field)"
          @input="onApprovalInput(field, $event)"
        />
        <select
          v-else-if="field.kind === 'boolean' || field.kind === 'select'"
          class="approval-select w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          :value="approvalFieldValue(field)"
          @change="onApprovalSelect(field, $event)"
        >
          <option value="">{{ field.required ? 'Select an option' : 'No selection' }}</option>
          <template v-if="field.kind === 'boolean'">
            <option value="true">True</option>
            <option value="false">False</option>
          </template>
          <template v-else>
            <option
              v-for="option in field.options ?? []"
              :key="`${field.id}-${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </template>
        </select>
        <select
          v-else-if="field.kind === 'multiselect'"
          class="approval-select min-h-28 w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          multiple
          :value="approvalFieldValue(field)"
          @change="onApprovalMultiSelect(field, $event)"
        >
          <option
            v-for="option in field.options ?? []"
            :key="`${field.id}-${option.value}`"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </label>
    </div>

    <p
      v-if="approvalUsesJsonFallback(activity)"
      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
    >
      JSON response
    </p>

    <Textarea
      v-if="approvalUsesJsonFallback(activity) && activity.approval"
      v-model="activity.approval.responseDraft"
      auto-resize
      fluid
      rows="5"
    />

    <p
      v-if="activity.approval?.validationError"
      class="m-0 text-[0.84rem] leading-[1.45] text-[#bc5f38]"
    >
      {{ activity.approval.validationError }}
    </p>

    <div
      v-if="activity.approval"
      class="flex flex-wrap gap-2"
    >
      <Button
        v-for="option in activity.approval.options"
        :key="`${activity.id}-${option.value}`"
        :label="option.label"
        size="small"
        :severity="option.value === 'decline' || option.value === 'cancel' ? 'secondary' : undefined"
        :outlined="option.value === 'decline' || option.value === 'cancel'"
        :disabled="Boolean(activity.approval.submittedDecision)"
        @click="submitApproval(option.value)"
      />
    </div>
    <p
      v-if="activity.approval?.submittedDecision"
      class="m-0 text-[0.8rem] text-[color:var(--app-text-soft)]"
    >
      Submitted {{ activity.approval.submittedDecision }}.
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import Button from 'primevue/button'

import type { ApprovalDecision, ApprovalFormFieldState, ChatActivity } from '../types/api'
import {
  approvalFieldPrompt,
  approvalFieldValue,
  approvalMessage,
  approvalSubmitPayload,
  clearApprovalValidation,
  updateApprovalFieldValue,
  updateApprovalMultiSelect,
} from './chat-timeline/helpers'

const props = defineProps<{
  activity: ChatActivity
  disabled?: boolean
}>()

const emit = defineEmits<{
  submitApproval: [requestId: string, decision: ApprovalDecision, contentText: string]
}>()

const approval = computed(() => props.activity.approval)
const baseFields = computed(() =>
  (approval.value?.formFields ?? []).filter((field) => !field.id.endsWith('__other')),
)
const singleField = computed(() => (baseFields.value.length === 1 ? baseFields.value[0] : null))
const headlineText = computed(() => {
  const message = approvalMessage(props.activity)?.trim()
  if (message) {
    return message
  }
  const field = singleField.value
  if (field) {
    const prompt = approvalFieldPrompt(field)?.trim()
    if (prompt) {
      return prompt
    }
    if (field.label.trim()) {
      return field.label.trim()
    }
  }
  return props.activity.detail.trim()
})

function companionOtherField(fieldId: string) {
  return approval.value?.formFields.find((field) => field.id === `${fieldId}__other`) ?? null
}

function normalizeText(value: string | null | undefined) {
  return (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase()
}

function shouldShowFieldLabel(field: ApprovalFormFieldState) {
  if (baseFields.value.length > 1) {
    return true
  }
  return normalizeText(field.label) !== normalizeText(headlineText.value)
}

function shouldShowFieldPrompt(field: ApprovalFormFieldState) {
  const prompt = approvalFieldPrompt(field)
  if (!prompt) {
    return false
  }
  if (baseFields.value.length > 1) {
    return true
  }
  return normalizeText(prompt) !== normalizeText(headlineText.value)
}

function onInput(field: ApprovalFormFieldState, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLInputElement)) {
    return
  }
  updateApprovalFieldValue(field, target.value)
  clearApprovalValidation(props.activity)
}

function onSelect(field: ApprovalFormFieldState, value: string) {
  updateApprovalFieldValue(field, value)
  const otherField = companionOtherField(field.id)
  if (otherField && value) {
    updateApprovalFieldValue(otherField, '')
  }
  clearApprovalValidation(props.activity)
}

function onMultiSelect(field: ApprovalFormFieldState, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  updateApprovalMultiSelect(field, target)
  clearApprovalValidation(props.activity)
}

function submit(decision: ApprovalDecision) {
  const payload = approvalSubmitPayload(props.activity, decision)
  if (!payload) {
    return
  }
  emit('submitApproval', payload.requestId, payload.decision, payload.contentText)
}
</script>

<template>
  <section
    class="rounded-[1.15rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,250,242,0.94)] px-3.5 py-3 shadow-[0_16px_40px_rgba(24,44,48,0.08),inset_0_0_0_1px_rgba(255,255,255,0.45)] max-sm:rounded-[1rem] max-sm:px-3 max-sm:py-2.5"
  >
    <div class="grid gap-2.5">
      <div class="grid gap-0.5">
        <p class="m-0 text-[0.96rem] font-semibold leading-[1.45] text-[color:var(--app-text)]">
          {{ headlineText }}
        </p>
      </div>

      <div
        v-for="field in baseFields"
        :key="`${activity.id}-${field.id}`"
        class="grid gap-2"
      >
        <div
          v-if="shouldShowFieldLabel(field) || shouldShowFieldPrompt(field)"
          class="grid gap-0.5"
        >
          <p
            v-if="shouldShowFieldLabel(field)"
            class="m-0 text-[0.88rem] font-semibold text-[color:var(--app-text)]"
          >
            {{ field.label }}
          </p>
          <p
            v-if="shouldShowFieldPrompt(field)"
            class="m-0 text-[0.8rem] leading-[1.45] text-[color:var(--app-text-soft)]"
          >
            {{ approvalFieldPrompt(field) }}
          </p>
        </div>

        <div
          v-if="field.kind === 'select' && (field.options?.length ?? 0) > 0"
          class="grid gap-1.5"
        >
          <button
            v-for="option in field.options ?? []"
            :key="`${field.id}-${option.value}`"
            type="button"
            :data-testid="`composer-user-input-option-${field.id}-${option.value}`"
            class="grid rounded-[0.95rem] border px-3 py-2.5 text-left transition"
            :class="
              approvalFieldValue(field) === option.value
                ? 'border-[rgba(21,94,99,0.4)] bg-[rgba(21,94,99,0.12)] shadow-[0_10px_24px_rgba(21,94,99,0.12)]'
                : 'border-[rgba(34,66,72,0.1)] bg-white/68 hover:border-[rgba(21,94,99,0.22)] hover:bg-white/88'
            "
            :disabled="disabled || Boolean(approval?.submittedDecision)"
            @click="onSelect(field, option.value)"
          >
            <span class="text-[0.9rem] font-semibold text-[color:var(--app-text)]">
              {{ option.label }}
            </span>
            <span
              v-if="option.description"
              class="mt-0.5 text-[0.78rem] leading-[1.4] text-[color:var(--app-text-soft)]"
            >
              {{ option.description }}
            </span>
          </button>
        </div>

        <input
          v-else-if="field.kind === 'text'"
          class="w-full rounded-[0.95rem] border border-[rgba(34,66,72,0.12)] bg-white/78 px-3 py-2.5 text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          type="text"
          :value="approvalFieldValue(field)"
          :disabled="disabled || Boolean(approval?.submittedDecision)"
          @input="onInput(field, $event)"
        />

        <select
          v-else-if="field.kind === 'boolean'"
          class="w-full rounded-[0.95rem] border border-[rgba(34,66,72,0.12)] bg-white/78 px-3 py-2.5 text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          :value="approvalFieldValue(field)"
          :disabled="disabled || Boolean(approval?.submittedDecision)"
          @change="onSelect(field, ($event.target as HTMLSelectElement).value)"
        >
          <option value="">Select an option</option>
          <option value="true">True</option>
          <option value="false">False</option>
        </select>

        <select
          v-else-if="field.kind === 'multiselect'"
          multiple
          class="min-h-28 w-full rounded-[0.95rem] border border-[rgba(34,66,72,0.12)] bg-white/78 px-3 py-2.5 text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
          :value="approvalFieldValue(field)"
          :disabled="disabled || Boolean(approval?.submittedDecision)"
          @change="onMultiSelect(field, $event)"
        >
          <option
            v-for="option in field.options ?? []"
            :key="`${field.id}-${option.value}`"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>

        <div
          v-if="companionOtherField(field.id)"
          class="grid gap-1 rounded-[0.95rem] border border-dashed border-[rgba(34,66,72,0.12)] bg-[rgba(255,255,255,0.5)] px-3 py-2.5"
        >
          <p class="m-0 text-[0.76rem] font-semibold text-[color:var(--app-text-soft)]">
            Or type your own answer
          </p>
          <input
            class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-white/78 px-3 py-2.5 text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
            type="text"
            :value="approvalFieldValue(companionOtherField(field.id)!)"
            :disabled="disabled || Boolean(approval?.submittedDecision)"
            @input="onInput(companionOtherField(field.id)!, $event)"
          />
        </div>
      </div>

      <p
        v-if="approval?.validationError"
        class="m-0 text-[0.84rem] leading-[1.5] text-[#bc5f38]"
      >
        {{ approval.validationError }}
      </p>

      <div class="flex items-center justify-between gap-3 pt-1">
        <p
          v-if="approval?.submittedDecision"
          class="m-0 text-[0.82rem] text-[color:var(--app-text-soft)]"
        >
          Submitted.
        </p>
        <div
          v-else
          class="flex items-center gap-2"
        >
          <Button
            label="Dismiss"
            severity="secondary"
            text
            data-testid="composer-user-input-dismiss"
            :disabled="disabled"
            @click="submit('cancel')"
          />
          <Button
            label="Submit"
            data-testid="composer-user-input-submit"
            :disabled="disabled"
            @click="submit('accept')"
          />
        </div>
      </div>
    </div>
  </section>
</template>

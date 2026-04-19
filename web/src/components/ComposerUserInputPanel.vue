<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import Textarea from 'primevue/textarea'

import type { ApprovalDecision, ApprovalFormFieldState, PendingRequest } from '../types/api'
import {
  approvalFieldPrompt,
  approvalFieldValue,
  approvalMessage,
  approvalSubmitPayload,
  clearApprovalValidation,
  createApprovalActivity,
  updateApprovalFieldValue,
  updateApprovalMultiSelect,
} from './chat-timeline/helpers'

const props = defineProps<{
  request: PendingRequest
  pendingRequest?: PendingRequest | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  submitRequest: [requestId: string, decision: ApprovalDecision, contentText: string]
}>()

const approvalActivity = ref(createApprovalActivity(props.request))
const approval = computed(() => approvalActivity.value.approval)
const isPlanImplementation = computed(() => props.request.kind === 'plan_implementation')
const planFeedback = ref('')

const baseFields = computed(() =>
  (approval.value?.formFields ?? []).filter((field) => !field.id.endsWith('__other')),
)
const singleField = computed(() => (baseFields.value.length === 1 ? baseFields.value[0] : null))
const headlineText = computed(() => {
  if (isPlanImplementation.value) {
    return props.request.title || 'Implement this plan?'
  }
  const message = approvalMessage(approvalActivity.value)?.trim()
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
  return props.request.detail.trim()
})

const planContent = computed(() => {
  const value = props.request.payload.planContent
  return typeof value === 'string' ? value : ''
})

watch(
  () => props.request,
  (request) => {
    approvalActivity.value = createApprovalActivity(request)
  },
)

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
  clearApprovalValidation(approvalActivity.value)
}

function onSelect(field: ApprovalFormFieldState, value: string) {
  updateApprovalFieldValue(field, value)
  const otherField = companionOtherField(field.id)
  if (otherField && value) {
    updateApprovalFieldValue(otherField, '')
  }
  clearApprovalValidation(approvalActivity.value)
}

function onMultiSelect(field: ApprovalFormFieldState, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  updateApprovalMultiSelect(field, target)
  clearApprovalValidation(approvalActivity.value)
}

function submit(decision: ApprovalDecision) {
  if (isPlanImplementation.value) {
    const content = {
      planContent: planContent.value,
      followupMessage: planFeedback.value.trim(),
    }
    emit('submitRequest', props.request.request_id, decision, JSON.stringify(content))
    return
  }

  const payload = approvalSubmitPayload(approvalActivity.value, decision)
  if (!payload) {
    return
  }
  emit('submitRequest', payload.requestId, payload.decision, payload.contentText)
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
        <p
          v-if="request.detail && request.detail !== headlineText"
          class="m-0 text-[0.82rem] leading-[1.45] text-[color:var(--app-text-soft)]"
        >
          {{ request.detail }}
        </p>
      </div>

      <template v-if="isPlanImplementation">
        <div
          v-if="planContent"
          class="max-h-64 overflow-auto rounded-[0.95rem] border border-[rgba(34,66,72,0.1)] bg-white/68 px-3 py-2.5 text-[0.86rem] leading-[1.55] text-[color:var(--app-text)]"
        >
          <pre class="m-0 whitespace-pre-wrap font-inherit">{{ planContent }}</pre>
        </div>

        <Textarea
          v-model="planFeedback"
          auto-resize
          fluid
          rows="3"
          placeholder="Optional: refine the plan before implementation..."
          :disabled="disabled"
        />
      </template>

      <template v-else>
        <div
          v-for="field in baseFields"
          :key="`${request.request_id}-${field.id}`"
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
              :disabled="disabled"
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
            :disabled="disabled"
            @input="onInput(field, $event)"
          />

          <select
            v-else-if="field.kind === 'boolean'"
            class="w-full rounded-[0.95rem] border border-[rgba(34,66,72,0.12)] bg-white/78 px-3 py-2.5 text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
            :value="approvalFieldValue(field)"
            :disabled="disabled"
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
            :disabled="disabled"
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
              :disabled="disabled"
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
      </template>

      <div class="flex items-center justify-between gap-3 pt-1">
        <p
          v-if="pendingRequest && pendingRequest.request_id !== request.request_id"
          class="m-0 text-[0.82rem] text-[color:var(--app-text-soft)]"
        >
          Another request is waiting ahead in the queue.
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
            :label="isPlanImplementation ? 'Implement plan' : 'Submit'"
            data-testid="composer-user-input-submit"
            :disabled="disabled"
            @click="submit('accept')"
          />
        </div>
      </div>
    </div>
  </section>
</template>

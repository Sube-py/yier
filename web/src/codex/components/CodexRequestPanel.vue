<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { CodexPendingRequest, CodexRequestQuestion, JsonRecord } from '../types'
import { compactJson } from '../lib/format'

const props = defineProps<{
  request: CodexPendingRequest | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  submitResponse: [requestId: string, response: JsonRecord]
}>()

const answers = reactive<Record<string, string>>({})
const currentQuestionIndex = ref(0)
const jsonDraft = ref('{\n  "answers": {}\n}')
const validationError = ref('')
const useJsonFallback = ref(false)

const questions = computed<CodexRequestQuestion[]>(() => {
  const rawQuestions = props.request?.params?.questions
  return Array.isArray(rawQuestions) ? rawQuestions.filter(isQuestion) : []
})
const supportsStructuredInput = computed(() => questions.value.length > 0)
const visibleAsJson = computed(() => !supportsStructuredInput.value || useJsonFallback.value)
const currentQuestion = computed(() => questions.value[currentQuestionIndex.value] ?? null)
const isFirstQuestion = computed(() => currentQuestionIndex.value === 0)
const isLastQuestion = computed(() => currentQuestionIndex.value >= questions.value.length - 1)

watch(
  () => props.request?.id,
  () => {
    for (const key of Object.keys(answers)) {
      delete answers[key]
    }
    currentQuestionIndex.value = 0
    validationError.value = ''
    useJsonFallback.value = false
    jsonDraft.value = '{\n  "answers": {}\n}'
  },
)

watch(
  () => questions.value.length,
  (questionCount) => {
    if (!questionCount) {
      currentQuestionIndex.value = 0
      return
    }
    if (currentQuestionIndex.value >= questionCount) {
      currentQuestionIndex.value = questionCount - 1
    }
  },
)

function isQuestion(value: unknown): value is CodexRequestQuestion {
  return (
    Boolean(value) &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    typeof (value as CodexRequestQuestion).id === 'string' &&
    Boolean((value as CodexRequestQuestion).id)
  )
}

function questionTitle(question: CodexRequestQuestion) {
  return question.header || question.question || question.id
}

function questionPrompt(question: CodexRequestQuestion) {
  return question.question && question.question !== questionTitle(question) ? question.question : ''
}

function optionKey(question: CodexRequestQuestion, optionLabel: string) {
  return `${question.id}-${optionLabel}`
}

function answerFor(question: CodexRequestQuestion) {
  return answers[question.id] ?? ''
}

function hasAnswer(question: CodexRequestQuestion) {
  return Boolean(answerFor(question).trim())
}

function selectAnswer(question: CodexRequestQuestion, value: string) {
  answers[question.id] = value
  validationError.value = ''
}

function selectOptionAnswer(question: CodexRequestQuestion, value: string) {
  selectAnswer(question, value)
  if (question.id === currentQuestion.value?.id && !isLastQuestion.value) {
    advanceQuestion()
  }
}

function validateCurrentQuestion() {
  const question = currentQuestion.value
  if (!question) {
    return true
  }
  if (hasAnswer(question)) {
    validationError.value = ''
    return true
  }
  validationError.value = `${questionTitle(question)} is required.`
  return false
}

function advanceQuestion() {
  if (!validateCurrentQuestion() || isLastQuestion.value) {
    return
  }
  currentQuestionIndex.value += 1
}

function goBack() {
  if (isFirstQuestion.value) {
    return
  }
  currentQuestionIndex.value -= 1
  validationError.value = ''
}

function submitEmpty() {
  const requestId = props.request?.id
  if (!requestId) {
    return
  }
  emit('submitResponse', requestId, { answers: {} })
}

function submitStructured() {
  const requestId = props.request?.id
  if (!requestId) {
    return
  }
  const responseAnswers: Record<string, { answers: string[] }> = {}
  for (const [index, question] of questions.value.entries()) {
    const answer = (answers[question.id] ?? '').trim()
    if (!answer) {
      currentQuestionIndex.value = index
      validationError.value = `${questionTitle(question)} is required.`
      return
    }
    responseAnswers[question.id] = { answers: [answer] }
  }
  emit('submitResponse', requestId, { answers: responseAnswers })
}

function submitJson() {
  const requestId = props.request?.id
  if (!requestId) {
    return
  }
  try {
    const parsed = JSON.parse(jsonDraft.value) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Response must be a JSON object.')
    }
    emit('submitResponse', requestId, parsed as JsonRecord)
  } catch (error) {
    validationError.value = error instanceof Error ? error.message : String(error)
  }
}
</script>

<template>
  <section v-if="request" class="border-t border-[color:var(--app-border)] bg-blue-50/70 px-4 py-3">
    <div class="mx-auto grid max-w-5xl gap-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-blue-700">
            User input required
          </p>
          <p class="m-0 mt-1 truncate text-sm font-semibold text-[color:var(--app-text)]">
            {{ request.params?.turnId || request.id }}
          </p>
        </div>
        <button
          v-if="supportsStructuredInput"
          type="button"
          class="inline-flex h-8 shrink-0 items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 text-xs font-semibold text-blue-700"
          :disabled="disabled"
          @click="useJsonFallback = !useJsonFallback"
        >
          <i class="pi pi-code text-[0.7rem]"></i>
          <span>{{ useJsonFallback ? 'Form' : 'JSON' }}</span>
        </button>
      </div>

      <div v-if="!visibleAsJson && currentQuestion" class="grid gap-3">
        <div class="grid gap-2 rounded-lg border border-blue-100 bg-white/76 px-3 py-2.5">
          <div class="flex items-start justify-between gap-3">
            <div class="grid min-w-0 gap-0.5">
              <p class="m-0 text-[0.7rem] font-bold uppercase tracking-[0.12em] text-blue-700">
                Question {{ currentQuestionIndex + 1 }} of {{ questions.length }}
              </p>
              <p class="m-0 text-sm font-semibold text-[color:var(--app-text)]">
                {{ questionTitle(currentQuestion) }}
              </p>
              <p
                v-if="questionPrompt(currentQuestion)"
                class="m-0 text-xs leading-5 text-[color:var(--app-text-soft)]"
              >
                {{ questionPrompt(currentQuestion) }}
              </p>
            </div>
            <div class="flex max-w-36 shrink-0 flex-wrap justify-end gap-1 pt-1" aria-hidden="true">
              <span
                v-for="(question, questionIndex) in questions"
                :key="question.id"
                class="h-1.5 w-5 rounded-full transition"
                :class="questionIndex <= currentQuestionIndex ? 'bg-blue-500' : 'bg-blue-100'"
              ></span>
            </div>
          </div>

          <div v-if="currentQuestion.options?.length" class="grid gap-1.5">
            <button
              v-for="option in currentQuestion.options"
              :key="optionKey(currentQuestion, option.label)"
              type="button"
              class="grid rounded-lg border px-3 py-2 text-left transition"
              :class="
                answerFor(currentQuestion) === option.label
                  ? 'border-blue-300 bg-blue-100'
                  : 'border-[color:var(--app-border)] bg-white hover:border-blue-200'
              "
              :disabled="disabled"
              @click="selectOptionAnswer(currentQuestion, option.label)"
            >
              <span class="text-sm font-semibold text-[color:var(--app-text)]">
                {{ option.label }}
              </span>
              <span
                v-if="option.description"
                class="mt-0.5 text-xs leading-5 text-[color:var(--app-text-soft)]"
              >
                {{ option.description }}
              </span>
            </button>
          </div>

          <input
            v-if="!currentQuestion.options?.length || currentQuestion.isOther"
            class="h-10 min-w-0 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm outline-none transition focus:border-blue-300"
            :type="currentQuestion.isSecret ? 'password' : 'text'"
            :value="answerFor(currentQuestion)"
            :disabled="disabled"
            :placeholder="currentQuestion.options?.length ? 'Other answer' : 'Answer'"
            @input="selectAnswer(currentQuestion, ($event.target as HTMLInputElement).value)"
            @keydown.enter.prevent="isLastQuestion ? submitStructured() : advanceQuestion()"
          />
        </div>
      </div>

      <div v-else class="grid gap-2">
        <textarea
          v-model="jsonDraft"
          class="min-h-32 w-full resize-y rounded-lg border border-blue-100 bg-white px-3 py-2 font-mono text-xs leading-5 outline-none focus:border-blue-300"
          :disabled="disabled"
        ></textarea>
        <pre
          class="max-h-44 overflow-auto rounded-lg bg-white/72 p-3 text-xs leading-5 text-[color:var(--app-text-soft)]"
          v-text="compactJson(request)"
        ></pre>
      </div>

      <p v-if="validationError" class="m-0 text-sm font-semibold text-red-700">
        {{ validationError }}
      </p>

      <div class="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          class="inline-flex h-9 items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 text-sm font-semibold text-blue-700 transition hover:bg-blue-50"
          :disabled="disabled"
          @click="submitEmpty"
        >
          <i class="pi pi-times text-xs"></i>
          <span>Dismiss</span>
        </button>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <button
            v-if="!visibleAsJson && !isFirstQuestion"
            type="button"
            class="inline-flex h-9 items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 text-sm font-semibold text-blue-700 transition hover:bg-blue-50"
            :disabled="disabled"
            @click="goBack"
          >
            <i class="pi pi-arrow-left text-xs"></i>
            <span>Back</span>
          </button>
          <button
            v-if="!visibleAsJson && !isLastQuestion"
            type="button"
            class="inline-flex h-9 items-center gap-2 rounded-lg bg-blue-700 px-3 text-sm font-semibold text-white transition hover:brightness-95 disabled:opacity-55"
            :disabled="disabled"
            @click="advanceQuestion"
          >
            <span>Next</span>
            <i class="pi pi-arrow-right text-xs"></i>
          </button>
          <button
            v-else
            type="button"
            class="inline-flex h-9 items-center gap-2 rounded-lg bg-blue-700 px-3 text-sm font-semibold text-white transition hover:brightness-95 disabled:opacity-55"
            :disabled="disabled"
            @click="visibleAsJson ? submitJson() : submitStructured()"
          >
            <i class="pi pi-send text-xs"></i>
            <span>Submit</span>
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

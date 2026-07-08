<script setup lang="ts">
defineProps<{
  message?: string
}>()
</script>

<template>
  <span class="codex-thinking-shimmer inline-flex min-w-0 items-center" data-codex-thinking-shimmer>
    <span class="min-w-0 truncate">{{ message || 'Thinking' }}</span>
  </span>
</template>

<style scoped>
.codex-thinking-shimmer {
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--app-text-soft) 72%, transparent) 0%,
    color-mix(in srgb, var(--app-text) 92%, transparent) 42%,
    color-mix(in srgb, var(--app-text-soft) 72%, transparent) 84%
  );
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-weight: 650;
  animation: codex-thinking-shimmer 1.45s ease-in-out infinite;
}

@keyframes codex-thinking-shimmer {
  0% {
    background-position: 120% 0;
    opacity: 0.48;
  }
  45% {
    opacity: 1;
  }
  100% {
    background-position: -120% 0;
    opacity: 0.58;
  }
}

@media (prefers-reduced-motion: reduce) {
  .codex-thinking-shimmer {
    animation: none;
    color: var(--app-text-soft);
  }
}
</style>

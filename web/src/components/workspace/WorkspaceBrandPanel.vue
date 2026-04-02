<script setup lang="ts">
const props = defineProps<{
  variant?: 'yier' | 'codex'
}>()
</script>

<template>
  <div
    class="brand-panel relative overflow-hidden rounded-[1.6rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.4rem] shadow-[var(--app-shadow)] backdrop-blur-[14px] after:pointer-events-none after:absolute after:top-[-3rem] after:right-[-3rem] after:h-40 after:w-40 after:rounded-full after:bg-[radial-gradient(circle,rgba(21,94,99,0.16),transparent_70%)] after:content-['']"
  >
    <p class="eyebrow">{{ props.variant === 'codex' ? 'yier x Codex' : 'Work anywhere' }}</p>
    <h1
      class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[2.8rem] leading-none font-semibold"
    >
      yier
    </h1>
    <p class="mt-3 mb-0 leading-[1.6] text-[color:var(--app-text-soft)]">
      {{
        props.variant === 'codex'
          ? 'Bring yier and Codex together so you can catch ideas anywhere and turn them into work right away.'
          : 'Use yier for chat, tools, and runtime control from anywhere.'
      }}
    </p>
  </div>
</template>

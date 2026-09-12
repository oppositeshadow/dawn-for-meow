<script setup lang="ts">
defineProps<{ title: string; confirmText?: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
    <div class="panel w-full max-w-lg animate-glow">
      <div class="panel-title justify-between">
        <span>{{ title }}</span>
        <button class="btn border-none text-terminal-dim" @click="emit('close')">✕</button>
      </div>
      <div class="space-y-3 p-4 text-[12px] leading-relaxed">
        <slot />
      </div>
      <div class="flex justify-end gap-2 border-t border-terminal-line px-4 py-2">
        <button class="btn btn-primary" @click="emit('close')">{{ confirmText ?? '知道了' }}</button>
      </div>
    </div>
  </div>
</template>

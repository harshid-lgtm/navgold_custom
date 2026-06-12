<template>
  <div
    v-if="visible"
    class="sticky top-0 z-50 bg-red-600 text-white font-bold text-center py-3 animate-pulse"
  >
    TELEMETRY LOST — backend unreachable. Silence is NOT health. Last known data shown.
  </div>
</template>

<script setup>
defineProps({
  visible: { type: Boolean, default: false }
})
</script>

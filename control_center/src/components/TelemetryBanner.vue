<template>
  <div v-if="visible" class="sticky top-0 z-50 animate-pulse">
    <Alert
      theme="red"
      variant="solid"
      title="TELEMETRY LOST — backend unreachable. Silence is NOT health. Last known data shown."
    />
  </div>
</template>

<script setup>
import { Alert } from 'frappe-ui'

defineProps({
  visible: { type: Boolean, default: false },
})
</script>

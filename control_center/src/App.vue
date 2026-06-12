<template>
  <FrappeUIProvider>
    <div class="min-h-screen bg-surface-white text-ink-gray-9">
      <router-view />
    </div>
  </FrappeUIProvider>
</template>

<script setup>
import { FrappeUIProvider } from 'frappe-ui'
</script>

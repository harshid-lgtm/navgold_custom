<template>
  <FrappeUIProvider>
    <div class="flex flex-col h-screen bg-surface-base text-ink-gray-9">
      <TelemetryBanner :visible="telemetry_lost" />
      <div class="flex flex-1 overflow-hidden">
        <AppSidebar />
        <main class="flex-1 overflow-y-auto">
          <router-view />
        </main>
      </div>
    </div>
  </FrappeUIProvider>
</template>

<script setup>
import { ref, provide, onMounted, onUnmounted, inject } from 'vue'
import { FrappeUIProvider } from 'frappe-ui'
import TelemetryBanner from './components/TelemetryBanner.vue'
import AppSidebar from './components/AppSidebar.vue'

const call = inject('$call')

const status_data = ref(null)
const telemetry_lost = ref(false)

async function fetch_status() {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.get_control_center_status')
    status_data.value = result
    telemetry_lost.value = false
  } catch (e) {
    telemetry_lost.value = true
  }
}

provide('status_data', status_data)
provide('telemetry_lost', telemetry_lost)
provide('refetch_status', fetch_status)

let status_interval = null

onMounted(() => {
  fetch_status()
  status_interval = setInterval(fetch_status, 10000)
})

onUnmounted(() => {
  clearInterval(status_interval)
})
</script>

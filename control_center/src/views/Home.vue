<template>
  <div class="min-h-screen bg-gray-950 text-white">
    <TelemetryBanner :visible="telemetry_lost" />
    <div class="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-3xl font-black tracking-tight text-white">Navgold Control Center</h1>
        <span class="text-xs text-gray-500">Last updated: {{ last_updated || 'Never' }}</span>
      </div>
      <StatusHeader
        :flags="status_data?.flags || {}"
        :is_blocked="is_blocked"
        @toggle-flag="on_toggle_flag"
      />
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-1">
          <PanicCard
            :snapshot="status_data?.snapshot || null"
            :freeze_active="status_data?.flags?.freeze_amazon_outbound || false"
            @action-complete="fetch_status"
          />
        </div>
        <div class="lg:col-span-2">
          <BlockersGrid :blockers="status_data?.blockers || {}" />
        </div>
      </div>
      <AnalyticsPanel :analytics="analytics_data" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import StatusHeader from '../components/StatusHeader.vue'
import PanicCard from '../components/PanicCard.vue'
import BlockersGrid from '../components/BlockersGrid.vue'
import AnalyticsPanel from '../components/AnalyticsPanel.vue'
import TelemetryBanner from '../components/TelemetryBanner.vue'

const call = inject('$call')

const status_data = ref(null)
const analytics_data = ref(null)
const telemetry_lost = ref(false)
const last_updated = ref('')

const is_blocked = computed(() => {
  const blockers = status_data.value?.blockers || {}
  const flags = status_data.value?.flags || {}
  return (
    flags.freeze_amazon_outbound ||
    Object.values(blockers).some((v, i) => {
      const keys = Object.keys(blockers)
      const key = keys[i]
      return key === 'minutes_since_last_order' ? v > 120 : v > 0
    })
  )
})

async function fetch_status() {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.get_control_center_status')
    status_data.value = result
    telemetry_lost.value = false
    last_updated.value = new Date().toLocaleTimeString()
  } catch (e) {
    telemetry_lost.value = true
  }
}

async function fetch_analytics() {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.get_order_analytics', { hours: 24 })
    analytics_data.value = result
    telemetry_lost.value = false
  } catch (e) {
    telemetry_lost.value = true
  }
}

async function on_toggle_flag(fieldname, value) {
  try {
    await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.toggle_flag', { fieldname, value })
    await fetch_status()
  } catch (e) {
    console.error('toggle_flag failed', e)
  }
}

let status_interval = null
let analytics_interval = null

onMounted(() => {
  fetch_status()
  fetch_analytics()
  status_interval = setInterval(fetch_status, 10000)
  analytics_interval = setInterval(fetch_analytics, 60000)
})

onUnmounted(() => {
  clearInterval(status_interval)
  clearInterval(analytics_interval)
})
</script>

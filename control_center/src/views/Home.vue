<template>
  <div class="flex flex-col h-full">
    <!-- Page header -->
    <header class="sticky top-0 z-10 flex min-h-12 items-center gap-3 border-b border-outline-gray-1 bg-surface-base px-3 sm:px-5">
      <h1 class="text-base font-semibold text-ink-gray-9 flex-1">Overview</h1>
      <Badge
        :label="is_blocked ? 'Blocked' : 'All clear'"
        :theme="is_blocked ? 'red' : 'green'"
        variant="subtle"
        size="sm"
      />
      <span v-if="last_updated" class="text-sm text-ink-gray-5">Updated {{ last_updated }}</span>
      <Button
        variant="subtle"
        size="sm"
        icon="lucide-refresh-cw"
        :loading="refreshing"
        @click="on_manual_refresh"
      />
    </header>

    <!-- Body -->
    <div class="px-3 sm:px-5 pt-5 pb-10 space-y-6">
      <!-- Read-only flag badge row -->
      <StatusHeader :flags="status_data?.flags || {}" />

      <!-- Blocker stat tiles -->
      <BlockersGrid :blockers="status_data?.blockers || {}" />

      <!-- Order analytics with charts -->
      <AnalyticsPanel />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject, watch } from 'vue'
import { Badge, Button } from 'frappe-ui'
import StatusHeader from '../components/StatusHeader.vue'
import BlockersGrid from '../components/BlockersGrid.vue'
import AnalyticsPanel from '../components/AnalyticsPanel.vue'

const status_data = inject('status_data')
const refetch_status = inject('refetch_status')

const last_updated = ref('')
const refreshing = ref(false)

const is_blocked = computed(() => {
  const blockers = status_data.value?.blockers || {}
  const flags = status_data.value?.flags || {}
  return (
    flags.freeze_amazon_outbound ||
    Object.entries(blockers).some(([key, value]) =>
      key === 'minutes_since_last_order' ? value > 120 : value > 0
    )
  )
})

watch(status_data, () => {
  last_updated.value = new Date().toLocaleTimeString()
})

async function on_manual_refresh() {
  refreshing.value = true
  await refetch_status()
  last_updated.value = new Date().toLocaleTimeString()
  refreshing.value = false
}
</script>

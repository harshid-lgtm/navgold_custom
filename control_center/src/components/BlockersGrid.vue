<template>
  <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
    <Tooltip
      v-for="card in blocker_cards"
      :key="card.key"
      :text="card.tooltip"
    >
      <div
        @click="window_open(card.link)"
        :class="[
          'bg-surface-white border rounded-xl p-4 cursor-pointer transition-colors',
          is_alarm(card)
            ? 'border-outline-red-3 blocker-alarm'
            : 'border-outline-gray-2 hover:bg-surface-gray-1',
        ]"
      >
        <div
          :class="[
            'text-4xl font-bold',
            is_alarm(card) ? 'text-ink-red-3' : 'text-ink-gray-9',
          ]"
        >
          {{ blockers[card.key] ?? '—' }}
        </div>
        <div class="text-xs text-ink-gray-4 uppercase tracking-wide mt-1">{{ card.label }}</div>
      </div>
    </Tooltip>
  </div>
</template>

<script setup>
import { Tooltip } from 'frappe-ui'

const props = defineProps({
  blockers: { type: Object, default: () => ({}) },
})

const blocker_cards = [
  {
    key: 'readiness_blocked',
    label: 'Not Ready',
    tooltip: 'Pricing items not ready for push',
    link: '/app/query-report/Amazon Pricing Push Readiness',
    alarm_threshold: 0,
  },
  {
    key: 'below_min_24h',
    label: 'Below Min (24h)',
    tooltip: 'Orders below minimum price in last 24 hours',
    link: '/app/query-report/Below Min Price Sales',
    alarm_threshold: 0,
  },
  {
    key: 'open_failed_syncs',
    label: 'Failed Syncs',
    tooltip: 'Open Amazon failed sync records',
    link: '/app/amazon-failed-sync-record?status=Failed',
    alarm_threshold: 0,
  },
  {
    key: 'stuck_feeds',
    label: 'Stuck Feeds',
    tooltip: 'Pricing feeds stuck in processing',
    link: '/app/query-report/Amazon Pricing Push Monitor',
    alarm_threshold: 0,
  },
  {
    key: 'errors_1h',
    label: 'Errors (1h)',
    tooltip: 'Error log entries in the last hour',
    link: '/app/error-log',
    alarm_threshold: 0,
  },
  {
    key: 'minutes_since_last_order',
    label: 'Mins Since Order',
    tooltip: 'Minutes since last Amazon order received — alarm if > 120',
    link: '/app/query-report/Amazon Order Sync Health',
    alarm_threshold: 120,
  },
]

function is_alarm(card) {
  const value = props.blockers[card.key]
  if (value === undefined || value === null) return false
  return value > card.alarm_threshold
}

function window_open(url) {
  window.open(url, '_blank')
}
</script>

<style scoped>
@keyframes blocker-pulse {
  0%, 100% { background-color: rgb(255, 241, 241); }
  50% { background-color: rgb(254, 226, 226); }
}
.blocker-alarm {
  animation: blocker-pulse 1.5s ease-in-out infinite;
}
</style>

<template>
  <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
    <div
      v-for="card in blocker_cards"
      :key="card.key"
      @click="window_open(card.link)"
      :class="is_alarm(card) ? 'bg-gray-900 rounded-xl p-4 border cursor-pointer border-red-500 bg-red-950 animate-pulse' : 'bg-gray-900 rounded-xl p-4 border cursor-pointer border-gray-700'"
    >
      <div :class="is_alarm(card) ? 'text-4xl font-black text-red-400' : 'text-4xl font-black text-white'">
        {{ blockers[card.key] ?? '—' }}
      </div>
      <div class="text-xs text-gray-400 uppercase tracking-wide mt-1">{{ card.label }}</div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  blockers: { type: Object, default: () => ({}) }
})

const blocker_cards = [
  { key: 'readiness_blocked', label: 'Not Ready', link: '/app/query-report/Amazon Pricing Push Readiness', alarm_threshold: 0 },
  { key: 'below_min_24h', label: 'Below Min (24h)', link: '/app/query-report/Below Min Price Sales', alarm_threshold: 0 },
  { key: 'open_failed_syncs', label: 'Failed Syncs', link: '/app/amazon-failed-sync-record?status=Failed', alarm_threshold: 0 },
  { key: 'stuck_feeds', label: 'Stuck Feeds', link: '/app/query-report/Amazon Pricing Push Monitor', alarm_threshold: 0 },
  { key: 'errors_1h', label: 'Errors (1h)', link: '/app/error-log', alarm_threshold: 0 },
  { key: 'minutes_since_last_order', label: 'Mins Since Order', link: '/app/query-report/Amazon Order Sync Health', alarm_threshold: 120 },
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

<template>
  <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
    <div
      v-for="card in blocker_cards"
      :key="card.key"
      @click="navigate_to(card)"
      :class="[
        'bg-surface-base rounded-md border cursor-pointer hover:bg-surface-gray-1 p-4 transition-colors',
        is_alarm(card)
          ? 'border-l-2 border-l-red-500 border-outline-gray-1'
          : 'border-outline-gray-1',
      ]"
    >
      <div :class="['text-2xl font-semibold', is_alarm(card) ? 'text-ink-red-6' : 'text-ink-gray-9']">
        {{ blockers[card.key] ?? '—' }}
      </div>
      <div class="text-sm text-ink-gray-5 mt-0.5">{{ card.label }}</div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const props = defineProps({
  blockers: { type: Object, default: () => ({}) },
})

const router = useRouter()

const blocker_cards = [
  {
    key: 'readiness_blocked',
    label: 'Not ready',
    route: '/push-readiness',
    alarm_threshold: 0,
  },
  {
    key: 'below_min_24h',
    label: 'Below min (24h)',
    route: '/below-min-sales',
    alarm_threshold: 0,
  },
  {
    key: 'open_failed_syncs',
    label: 'Failed syncs',
    external_link: '/app/amazon-failed-sync-record?status=Failed',
    alarm_threshold: 0,
  },
  {
    key: 'stuck_feeds',
    label: 'Stuck feeds',
    route: '/pricing-push',
    alarm_threshold: 0,
  },
  {
    key: 'errors_1h',
    label: 'Errors (1h)',
    external_link: '/app/error-log',
    alarm_threshold: 0,
  },
  {
    key: 'minutes_since_last_order',
    label: 'Mins since order',
    route: '/order-sync',
    alarm_threshold: 120,
  },
]

function is_alarm(card) {
  const value = props.blockers[card.key]
  if (value === undefined || value === null) return false
  return value > card.alarm_threshold
}

function navigate_to(card) {
  if (card.external_link) {
    window.open(card.external_link, '_blank')
    return
  }
  if (card.route) {
    router.push(card.route)
  }
}
</script>

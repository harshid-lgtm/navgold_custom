<template>
  <div class="space-y-4">
    <div class="flex justify-center">
      <span
        :class="is_blocked
          ? 'bg-red-600 text-white animate-pulse text-2xl font-black uppercase tracking-widest px-8 py-4 rounded-full'
          : 'bg-green-600 text-white text-2xl font-black uppercase tracking-widest px-8 py-4 rounded-full'"
      >
        {{ is_blocked ? 'GO-LIVE BLOCKED' : 'ALL CLEAR' }}
      </span>
    </div>
    <div class="flex flex-wrap gap-2 justify-center">
      <button
        v-for="flag_def in flag_definitions"
        :key="flag_def.key"
        @click="on_pill_click(flag_def)"
        :class="get_pill_class(flag_def)"
      >
        {{ flag_def.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  flags: { type: Object, default: () => ({}) },
  is_blocked: { type: Boolean, default: false }
})

const emit = defineEmits(['toggle-flag'])

const flag_definitions = [
  { key: 'enable_pricing_sync', label: 'Pricing Sync' },
  { key: 'repricer_enabled', label: 'Repricer' },
  { key: 'freeze_amazon_outbound', label: 'FREEZE', is_kill_switch: true },
  { key: 'pause_strategy_refresh', label: 'Strategy Refresh' },
  { key: 'enable_afn_stock_sync', label: 'AFN Stock' },
  { key: 'amazon_publishing_mode', label: 'Publishing Mode' },
  { key: 'enable_sync', label: 'Sync' },
]

function get_pill_class(flag_def) {
  const active = !!props.flags[flag_def.key]
  if (flag_def.is_kill_switch) {
    return active
      ? 'bg-red-900 border-2 border-red-500 text-red-200 font-black text-base px-5 py-2 rounded cursor-pointer'
      : 'bg-gray-800 border border-gray-600 text-gray-300 text-base px-5 py-2 rounded cursor-pointer'
  }
  return active
    ? 'bg-green-700 text-white text-sm px-4 py-2 rounded cursor-pointer'
    : 'bg-gray-800 text-gray-400 text-sm px-4 py-2 rounded cursor-pointer'
}

function on_pill_click(flag_def) {
  const current_value = !!props.flags[flag_def.key]
  const confirmed = confirm(`Toggle ${flag_def.label}?`)
  if (confirmed) {
    emit('toggle-flag', flag_def.key, !current_value)
  }
}
</script>

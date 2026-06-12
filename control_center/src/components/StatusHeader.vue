<template>
  <div class="flex flex-wrap gap-1.5">
    <Badge
      v-for="flag_def in flag_definitions"
      :key="flag_def.key"
      :label="flag_def.label"
      :theme="flags[flag_def.key] ? 'green' : 'gray'"
      variant="subtle"
      size="sm"
    />
  </div>
</template>

<script setup>
import { Badge } from 'frappe-ui'

defineProps({
  flags: { type: Object, default: () => ({}) },
})

const flag_definitions = [
  { key: 'enable_pricing_sync', label: 'Pricing Sync' },
  { key: 'repricer_enabled', label: 'Repricer' },
  { key: 'freeze_amazon_outbound', label: 'Freeze' },
  { key: 'pause_strategy_refresh', label: 'Strategy Refresh' },
  { key: 'enable_afn_stock_sync', label: 'AFN Stock' },
  { key: 'amazon_publishing_mode', label: 'Publishing Mode' },
  { key: 'enable_sync', label: 'Sync' },
]
</script>

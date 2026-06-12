<template>
  <div class="space-y-4">
    <!-- Status badge — prominent, centered -->
    <div class="flex justify-center">
      <Badge
        :theme="is_blocked ? 'red' : 'green'"
        variant="solid"
        size="lg"
        :label="is_blocked ? 'GO-LIVE BLOCKED' : 'ALL CLEAR'"
        :class="is_blocked ? 'animate-pulse text-base px-6 py-2' : 'text-base px-6 py-2'"
      />
    </div>

    <!-- Feature flag toggles -->
    <div class="flex flex-wrap gap-2 justify-center">
      <Button
        v-for="flag_def in flag_definitions"
        :key="flag_def.key"
        :variant="get_button_variant(flag_def)"
        :theme="get_button_theme(flag_def)"
        :label="flag_def.label"
        size="sm"
        @click="on_pill_click(flag_def)"
      />
    </div>

    <!-- Confirm toggle Dialog -->
    <Dialog
      v-model="confirm_dialog_open"
      :options="{ title: 'Confirm Flag Toggle', size: 'sm' }"
    >
      <template #body-content>
        <p class="text-p-base text-ink-gray-7 mb-4">
          Are you sure you want to toggle
          <strong class="text-ink-gray-9">{{ pending_flag?.label }}</strong>?
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="subtle" label="Cancel" @click="confirm_dialog_open = false" />
          <Button
            :theme="pending_flag?.is_kill_switch ? 'red' : 'blue'"
            variant="solid"
            label="Confirm"
            @click="confirm_toggle"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Badge, Button, Dialog } from 'frappe-ui'

const props = defineProps({
  flags: { type: Object, default: () => ({}) },
  is_blocked: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle-flag'])

const confirm_dialog_open = ref(false)
const pending_flag = ref(null)

const flag_definitions = [
  { key: 'enable_pricing_sync', label: 'Pricing Sync' },
  { key: 'repricer_enabled', label: 'Repricer' },
  { key: 'freeze_amazon_outbound', label: 'FREEZE', is_kill_switch: true },
  { key: 'pause_strategy_refresh', label: 'Strategy Refresh' },
  { key: 'enable_afn_stock_sync', label: 'AFN Stock' },
  { key: 'amazon_publishing_mode', label: 'Publishing Mode' },
  { key: 'enable_sync', label: 'Sync' },
]

function get_button_variant(flag_def) {
  const active = !!props.flags[flag_def.key]
  return active ? 'solid' : 'subtle'
}

function get_button_theme(flag_def) {
  const active = !!props.flags[flag_def.key]
  if (flag_def.is_kill_switch) return active ? 'red' : 'gray'
  return active ? 'green' : 'gray'
}

function on_pill_click(flag_def) {
  pending_flag.value = flag_def
  confirm_dialog_open.value = true
}

function confirm_toggle() {
  const current_value = !!props.flags[pending_flag.value.key]
  emit('toggle-flag', pending_flag.value.key, !current_value)
  confirm_dialog_open.value = false
  pending_flag.value = null
}
</script>

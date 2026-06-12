<template>
  <div class="bg-gray-900 border border-gray-700 rounded-xl p-6 space-y-4">
    <h2 class="text-red-400 font-black text-lg uppercase tracking-wide">Emergency Controls</h2>

    <div class="space-y-2">
      <label class="text-xs text-gray-400 uppercase tracking-wide">Type PANIC to enable emergency actions</label>
      <input
        v-model="panic_input"
        type="text"
        placeholder="PANIC"
        class="w-full bg-gray-800 border border-gray-600 text-white rounded px-3 py-2 text-sm focus:outline-none focus:border-red-500"
      />
    </div>

    <div class="flex gap-3">
      <button
        :disabled="!panic_confirmed"
        @click="on_freeze"
        class="bg-red-800 hover:bg-red-700 text-white font-bold px-6 py-3 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed"
      >
        Freeze All Outbound
      </button>
      <button
        :disabled="!panic_confirmed || !snapshot"
        @click="on_restore"
        :title="!snapshot ? 'No snapshot available' : ''"
        class="bg-orange-800 hover:bg-orange-700 text-white font-bold px-6 py-3 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed"
      >
        Restore Snapshot + Freeze
      </button>
    </div>

    <div v-if="action_result" class="bg-gray-800 rounded p-3 text-sm text-green-300 whitespace-pre-wrap">{{ action_result }}</div>

    <hr class="border-gray-700" />

    <div class="space-y-2">
      <h3 class="text-sm font-semibold text-gray-300 uppercase tracking-wide">Price Snapshot</h3>
      <div v-if="snapshot" class="text-sm space-y-1 text-gray-300">
        <div><span class="text-gray-500">Name:</span> {{ snapshot.name }}</div>
        <div><span class="text-gray-500">Taken:</span> {{ snapshot.taken_at }}</div>
        <div><span class="text-gray-500">SKUs:</span> {{ snapshot.sku_count }}</div>
        <div><span class="text-gray-500">Status:</span> {{ snapshot.status }}</div>
      </div>
      <div v-else class="text-sm text-gray-500 italic">No snapshot captured</div>
      <button
        @click="on_capture_snapshot"
        :disabled="capturing_snapshot"
        class="bg-blue-800 hover:bg-blue-700 text-white font-semibold px-4 py-2 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ capturing_snapshot ? 'Capturing...' : 'Capture Price Snapshot' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject } from 'vue'

const props = defineProps({
  snapshot: { type: Object, default: null },
  freeze_active: { type: Boolean, default: false }
})

const emit = defineEmits(['action-complete'])
const call = inject('$call')

const panic_input = ref('')
const action_result = ref('')
const capturing_snapshot = ref(false)

const panic_confirmed = computed(() => panic_input.value === 'PANIC')

async function on_freeze() {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.panic_button', { restore: 0 })
    action_result.value = result?.log || 'Freeze initiated.'
    panic_input.value = ''
    emit('action-complete')
  } catch (e) {
    action_result.value = 'Error: ' + (e?.message || 'Unknown error')
  }
}

async function on_restore() {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.panic_button', { restore: 1 })
    action_result.value = result?.log || 'Restore + Freeze initiated.'
    panic_input.value = ''
    emit('action-complete')
  } catch (e) {
    action_result.value = 'Error: ' + (e?.message || 'Unknown error')
  }
}

async function on_capture_snapshot() {
  capturing_snapshot.value = true
  try {
    await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.capture_price_snapshot')
    emit('action-complete')
  } catch (e) {
    console.error('capture_price_snapshot failed', e)
  } finally {
    capturing_snapshot.value = false
  }
}
</script>

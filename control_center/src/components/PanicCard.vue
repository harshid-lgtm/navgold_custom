<template>
  <div class="bg-surface-white border border-outline-gray-2 rounded-xl p-6 space-y-4">
    <h2 class="text-base font-semibold text-ink-red-3 uppercase tracking-wide">Emergency Controls</h2>

    <FormControl
      label="Type PANIC to enable emergency actions"
      type="text"
      placeholder="PANIC"
      v-model="panic_input"
      :variant="panic_confirmed ? 'outline' : 'subtle'"
    />

    <div class="flex gap-3 flex-wrap">
      <Button
        theme="red"
        variant="solid"
        label="Freeze All Outbound"
        :disabled="!panic_confirmed"
        @click="open_freeze_dialog"
      />
      <Button
        theme="red"
        variant="subtle"
        label="Restore Snapshot + Freeze"
        :disabled="!panic_confirmed || !snapshot"
        :title="!snapshot ? 'No snapshot available' : ''"
        @click="open_restore_dialog"
      />
    </div>

    <div class="border-t border-outline-gray-2 pt-4 space-y-2">
      <h3 class="text-sm font-semibold text-ink-gray-7 uppercase tracking-wide">Price Snapshot</h3>
      <div v-if="snapshot" class="text-sm space-y-1 text-ink-gray-6">
        <div><span class="text-ink-gray-4">Name:</span> {{ snapshot.name }}</div>
        <div><span class="text-ink-gray-4">Taken:</span> {{ snapshot.taken_at }}</div>
        <div><span class="text-ink-gray-4">SKUs:</span> {{ snapshot.sku_count }}</div>
        <div>
          <span class="text-ink-gray-4">Status:</span>
          <Badge :label="snapshot.status" theme="blue" variant="subtle" size="sm" class="ml-1" />
        </div>
      </div>
      <div v-else class="text-sm text-ink-gray-4 italic">No snapshot captured</div>
      <Button
        variant="subtle"
        label="Capture Price Snapshot"
        :loading="capturing_snapshot"
        @click="on_capture_snapshot"
      />
    </div>

    <!-- PANIC: Freeze confirmation dialog -->
    <Dialog
      v-model="freeze_dialog_open"
      :options="{ title: 'Confirm Freeze', size: 'sm' }"
    >
      <template #body-content>
        <p class="text-p-base text-ink-gray-7 mb-4">
          This will freeze all Amazon outbound actions. Are you sure?
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="subtle" label="Cancel" @click="freeze_dialog_open = false" />
          <Button theme="red" variant="solid" label="Freeze All Outbound" :loading="action_loading" @click="on_freeze" />
        </div>
      </template>
    </Dialog>

    <!-- PANIC: Restore + Freeze confirmation dialog -->
    <Dialog
      v-model="restore_dialog_open"
      :options="{ title: 'Confirm Restore + Freeze', size: 'sm' }"
    >
      <template #body-content>
        <p class="text-p-base text-ink-gray-7 mb-4">
          This will restore the last price snapshot and freeze all Amazon outbound. Are you sure?
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="subtle" label="Cancel" @click="restore_dialog_open = false" />
          <Button theme="red" variant="solid" label="Restore + Freeze" :loading="action_loading" @click="on_restore" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, computed, inject } from 'vue'
import { Button, Dialog, FormControl, Badge, toast } from 'frappe-ui'

const props = defineProps({
  snapshot: { type: Object, default: null },
  freeze_active: { type: Boolean, default: false },
})

const emit = defineEmits(['action-complete'])
const call = inject('$call')

const panic_input = ref('')
const capturing_snapshot = ref(false)
const action_loading = ref(false)
const freeze_dialog_open = ref(false)
const restore_dialog_open = ref(false)

const panic_confirmed = computed(() => panic_input.value === 'PANIC')

function open_freeze_dialog() {
  freeze_dialog_open.value = true
}

function open_restore_dialog() {
  restore_dialog_open.value = true
}

async function on_freeze() {
  action_loading.value = true
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.panic_button', { restore: 0 })
    toast.success(result?.log || 'Freeze initiated.')
    panic_input.value = ''
    freeze_dialog_open.value = false
    emit('action-complete')
  } catch (e) {
    toast.error('Freeze failed: ' + (e?.message || 'Unknown error'))
  } finally {
    action_loading.value = false
  }
}

async function on_restore() {
  action_loading.value = true
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.panic_button', { restore: 1 })
    toast.success(result?.log || 'Restore + Freeze initiated.')
    panic_input.value = ''
    restore_dialog_open.value = false
    emit('action-complete')
  } catch (e) {
    toast.error('Restore failed: ' + (e?.message || 'Unknown error'))
  } finally {
    action_loading.value = false
  }
}

async function on_capture_snapshot() {
  capturing_snapshot.value = true
  try {
    await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.capture_price_snapshot')
    toast.success('Price snapshot captured.')
    emit('action-complete')
  } catch (e) {
    toast.error('Snapshot failed: ' + (e?.message || 'Unknown error'))
  } finally {
    capturing_snapshot.value = false
  }
}
</script>

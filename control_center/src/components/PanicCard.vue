<template>
  <div class="space-y-4">
    <!-- Snapshot card -->
    <div class="bg-surface-base rounded-md border border-outline-gray-1 p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-base font-semibold text-ink-gray-9">Price snapshot</h3>
        <Button
          variant="subtle"
          size="sm"
          icon-left="lucide-camera"
          label="Capture"
          :loading="capturing_snapshot"
          @click="on_capture_snapshot"
        />
      </div>
      <div v-if="snapshot" class="space-y-1 text-sm">
        <div class="flex items-center gap-2 text-ink-gray-7">
          <span class="text-ink-gray-5 w-12 shrink-0">Name</span>{{ snapshot.name }}
        </div>
        <div class="flex items-center gap-2 text-ink-gray-7">
          <span class="text-ink-gray-5 w-12 shrink-0">Taken</span>{{ snapshot.taken_at }}
        </div>
        <div class="flex items-center gap-2 text-ink-gray-7">
          <span class="text-ink-gray-5 w-12 shrink-0">SKUs</span>{{ snapshot.sku_count }}
        </div>
        <div class="flex items-center gap-2">
          <span class="text-ink-gray-5 w-12 shrink-0">Status</span>
          <Badge :label="snapshot.status" theme="blue" variant="subtle" size="sm" />
        </div>
      </div>
      <p v-else class="text-sm text-ink-gray-5">No snapshot captured yet.</p>
    </div>

    <!-- Actions -->
    <div class="flex gap-2 flex-wrap">
      <Button
        theme="red"
        variant="solid"
        size="sm"
        label="Freeze all outbound"
        @click="freeze_dialog_open = true"
      />
      <Button
        theme="red"
        variant="subtle"
        size="sm"
        label="Restore snapshot + freeze"
        :disabled="!snapshot"
        @click="restore_dialog_open = true"
      />
    </div>

    <!-- Freeze dialog with typed confirm -->
    <Dialog v-model:open="freeze_dialog_open" :options="{ title: 'Freeze all outbound', size: 'sm' }">
      <template #body-content>
        <p class="text-p-sm text-ink-gray-7 mb-4">
          This stops all outbound writes to Amazon and Repricer. Type <strong>FREEZE</strong> to confirm.
        </p>
        <FormControl v-model="freeze_confirm_text" type="text" placeholder="FREEZE" />
        <div class="flex justify-end gap-2 mt-4">
          <Button
            variant="subtle"
            size="sm"
            label="Cancel"
            @click="freeze_dialog_open = false; freeze_confirm_text = ''"
          />
          <Button
            theme="red"
            variant="solid"
            size="sm"
            label="Freeze"
            :disabled="freeze_confirm_text !== 'FREEZE'"
            :loading="action_loading"
            @click="on_freeze"
          />
        </div>
      </template>
    </Dialog>

    <!-- Restore dialog with typed confirm -->
    <Dialog v-model:open="restore_dialog_open" :options="{ title: 'Restore snapshot + freeze', size: 'sm' }">
      <template #body-content>
        <p class="text-p-sm text-ink-gray-7 mb-4">
          Restores the last price snapshot and freezes all Amazon outbound. Type <strong>RESTORE</strong> to confirm.
        </p>
        <FormControl v-model="restore_confirm_text" type="text" placeholder="RESTORE" />
        <div class="flex justify-end gap-2 mt-4">
          <Button
            variant="subtle"
            size="sm"
            label="Cancel"
            @click="restore_dialog_open = false; restore_confirm_text = ''"
          />
          <Button
            theme="red"
            variant="solid"
            size="sm"
            label="Restore + freeze"
            :disabled="restore_confirm_text !== 'RESTORE'"
            :loading="action_loading"
            @click="on_restore"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, inject } from 'vue'
import { Button, Dialog, FormControl, Badge, toast } from 'frappe-ui'

const props = defineProps({
  snapshot: { type: Object, default: null },
})

const emit = defineEmits(['action-complete'])
const call = inject('$call')

const capturing_snapshot = ref(false)
const action_loading = ref(false)
const freeze_dialog_open = ref(false)
const restore_dialog_open = ref(false)
const freeze_confirm_text = ref('')
const restore_confirm_text = ref('')

async function on_freeze() {
  action_loading.value = true
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.panic_button', { restore: 0 })
    toast.success(result?.log || 'Freeze initiated.')
    freeze_confirm_text.value = ''
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
    restore_confirm_text.value = ''
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

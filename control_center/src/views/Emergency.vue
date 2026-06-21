<template>
  <div class="flex flex-col h-full">
    <!-- Page header -->
    <header class="sticky top-0 z-10 flex min-h-12 items-center border-b border-outline-gray-1 bg-surface-base px-3 sm:px-5">
      <h1 class="text-base font-semibold text-ink-gray-9">Emergency controls</h1>
    </header>

    <!-- Body -->
    <div class="px-3 sm:px-5 pt-5 pb-10 space-y-6 max-w-2xl">
      <p class="text-p-sm text-ink-gray-7">
        Emergency controls stop all outbound writes to Amazon and Repricer. Use them when pricing data is corrupted or the system is misbehaving.
      </p>

      <!-- Panic card -->
      <PanicCard
        :snapshot="status_data?.snapshot || null"
        @action-complete="refetch_status"
      />

      <!-- Feature flags -->
      <div class="space-y-3">
        <h2 class="text-base font-semibold text-ink-gray-9">Feature flags</h2>
        <div class="bg-surface-base rounded-md border border-outline-gray-1 divide-y divide-outline-gray-1">
          <div
            v-for="flag_def in flag_definitions"
            :key="flag_def.key"
            class="flex items-center justify-between px-4 py-3"
          >
            <div class="flex items-center gap-2">
              <span class="text-sm text-ink-gray-9">{{ flag_def.label }}</span>
              <Badge
                :label="is_flag_active(flag_def.key) ? 'On' : 'Off'"
                :theme="is_flag_active(flag_def.key) ? 'green' : 'gray'"
                variant="subtle"
                size="sm"
              />
            </div>
            <Button
              :theme="is_flag_active(flag_def.key) ? 'red' : 'gray'"
              variant="subtle"
              size="sm"
              :label="is_flag_active(flag_def.key) ? 'Disable' : 'Enable'"
              @click="open_flag_dialog(flag_def)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- Flag toggle confirm dialog -->
    <Dialog v-model:open="flag_dialog_open" title="Confirm flag change" size="sm">
      <template #body-content>
        <p class="text-p-sm text-ink-gray-7 mb-4">
          Are you sure you want to
          <strong>{{ is_flag_active(pending_flag?.key) ? 'disable' : 'enable' }}</strong>
          <strong class="text-ink-gray-9"> {{ pending_flag?.label }}</strong>?
        </p>
        <div class="flex justify-end gap-2">
          <Button variant="subtle" size="sm" label="Cancel" @click="flag_dialog_open = false" />
          <Button
            :theme="is_flag_active(pending_flag?.key) ? 'red' : 'blue'"
            variant="solid"
            size="sm"
            label="Confirm"
            :loading="flag_loading"
            @click="confirm_flag_toggle"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, inject } from 'vue'
import { Badge, Button, Dialog, toast } from 'frappe-ui'
import PanicCard from '../components/PanicCard.vue'
import { FLAG_DEFINITIONS } from '../constants'

const status_data = inject('status_data')
const refetch_status = inject('refetch_status')
const call = inject('$call')

const flag_dialog_open = ref(false)
const flag_loading = ref(false)
const pending_flag = ref(null)

const flag_definitions = FLAG_DEFINITIONS

function is_flag_active(key) {
  return !!status_data.value?.flags?.[key]
}

function open_flag_dialog(flag_def) {
  pending_flag.value = flag_def
  flag_dialog_open.value = true
}

async function confirm_flag_toggle() {
  if (!pending_flag.value) return
  flag_loading.value = true
  try {
    const new_value = !is_flag_active(pending_flag.value.key)
    await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.toggle_flag', {
      fieldname: pending_flag.value.key,
      value: new_value,
    })
    toast.success(`${pending_flag.value.label} ${new_value ? 'enabled' : 'disabled'}.`)
    flag_dialog_open.value = false
    pending_flag.value = null
    await refetch_status()
  } catch (e) {
    toast.error('Failed to toggle flag: ' + (e?.message || 'Unknown error'))
  } finally {
    flag_loading.value = false
  }
}
</script>

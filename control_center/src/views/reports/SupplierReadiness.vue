<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      ref="panel_ref"
      report_name="Supplier Data Readiness"
      description="Suppliers with stale pricing or missing lead time data."
      :default_filters="{ staleness_days: 14 }"
    >
      <template #filters>
        <div class="flex items-center gap-2">
          <FormControl
            v-model="staleness_days_str"
            type="number"
            placeholder="14"
            class="w-16"
            @change="on_filter_change"
          />
          <span class="text-sm text-ink-gray-5">days stale</span>
        </div>
      </template>
    </ReportPanel>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { FormControl } from 'frappe-ui'
import ReportPanel from '../../components/ReportPanel.vue'
import { to_int } from '../../utils/format'

const panel_ref = ref(null)
const staleness_days_str = ref('14')

function on_filter_change() {
  if (panel_ref.value) {
    panel_ref.value.filters.staleness_days = to_int(staleness_days_str.value, 14)
    panel_ref.value.fetch_report()
  }
}
</script>

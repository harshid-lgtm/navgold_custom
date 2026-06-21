<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      ref="panel_ref"
      report_name="Below Min Price Sales"
      description="Sales orders where the selling price was below the minimum threshold."
      :default_filters="{ days_back: 7, only_below_min: 1 }"
    >
      <template #filters>
        <div class="flex items-center gap-2">
          <FormControl
            v-model="days_back_str"
            type="number"
            placeholder="7"
            class="w-16"
            @change="on_filter_change"
          />
          <span class="text-sm text-ink-gray-5">days</span>
          <Checkbox
            v-model="only_below_min"
            label="Below min only"
            @update:model-value="on_filter_change"
          />
        </div>
      </template>
    </ReportPanel>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Checkbox, FormControl } from 'frappe-ui'
import ReportPanel from '../../components/ReportPanel.vue'
import { to_int } from '../../utils/format'

const panel_ref = ref(null)
const days_back_str = ref('7')
const only_below_min = ref(true)

function on_filter_change() {
  if (panel_ref.value) {
    panel_ref.value.filters.days_back = to_int(days_back_str.value, 7)
    panel_ref.value.filters.only_below_min = only_below_min.value ? 1 : 0
    panel_ref.value.fetch_report()
  }
}
</script>

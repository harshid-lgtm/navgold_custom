<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      ref="panel_ref"
      report_name="Amazon Pricing Push Monitor"
      description="Recent pricing feed submissions and their processing status."
      :default_filters="{ hours_back: hours_back, status: status_filter }"
    >
      <template #filters>
        <div class="flex items-center gap-2">
          <FormControl
            v-model="hours_back_str"
            type="text"
            placeholder="24"
            class="w-16"
            @change="on_filter_change"
          />
          <span class="text-sm text-ink-gray-5">hrs</span>
          <FormControl
            v-model="status_filter"
            type="select"
            :options="status_options"
            class="w-28"
            @change="on_filter_change"
          />
        </div>
      </template>
    </ReportPanel>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { FormControl } from 'frappe-ui'
import ReportPanel from '../../components/ReportPanel.vue'

const panel_ref = ref(null)
const hours_back = ref(24)
const hours_back_str = ref('24')
const status_filter = ref('')

const status_options = [
  { label: 'All', value: '' },
  { label: 'Pending', value: 'Pending' },
  { label: 'Processing', value: 'Processing' },
  { label: 'Done', value: 'Done' },
  { label: 'Failed', value: 'Failed' },
]

function on_filter_change() {
  hours_back.value = parseInt(hours_back_str.value) || 24
  if (panel_ref.value) {
    panel_ref.value.filters.hours_back = hours_back.value
    panel_ref.value.filters.status = status_filter.value
    panel_ref.value.fetch_report()
  }
}
</script>

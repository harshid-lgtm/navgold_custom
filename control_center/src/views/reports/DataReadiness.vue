<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      ref="panel_ref"
      report_name="Amazon Go-Live Data Readiness"
      description="Items missing required fields for Amazon listing and pricing."
      :default_filters="{ only_issues: 1 }"
    >
      <template #filters>
        <label class="flex items-center gap-1.5 text-sm text-ink-gray-7 cursor-pointer">
          <input
            type="checkbox"
            v-model="only_issues"
            @change="on_filter_change"
            class="rounded border-outline-gray-1"
          />
          Issues only
        </label>
      </template>
    </ReportPanel>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ReportPanel from '../../components/ReportPanel.vue'

const panel_ref = ref(null)
const only_issues = ref(true)

function on_filter_change() {
  if (panel_ref.value) {
    panel_ref.value.filters.only_issues = only_issues.value ? 1 : 0
    panel_ref.value.fetch_report()
  }
}
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Panel header -->
    <header class="sticky top-0 z-10 flex min-h-12 items-center gap-3 border-b border-outline-gray-1 bg-surface-base px-3 sm:px-5">
      <h2 class="text-base font-semibold text-ink-gray-9 flex-1">{{ report_name }}</h2>
      <Badge v-if="row_count !== null" :label="String(row_count)" theme="gray" variant="subtle" size="sm" />
      <slot name="filters" />
      <Button variant="subtle" size="sm" icon="lucide-refresh-cw" :loading="loading" @click="fetch_report" />
      <Button
        variant="ghost"
        size="sm"
        icon-left="lucide-external-link"
        label="Open in Desk"
        @click="open_in_desk"
      />
    </header>

    <p v-if="description" class="px-3 sm:px-5 pt-3 text-p-sm text-ink-gray-5">{{ description }}</p>

    <!-- Loading skeleton -->
    <div v-if="loading && !columns.length" class="flex items-center justify-center py-16">
      <span class="lucide-loader-circle size-5 text-ink-gray-5 animate-spin" aria-hidden="true" />
    </div>

    <!-- Empty state -->
    <div
      v-else-if="!loading && !rows.length"
      class="flex flex-col items-center justify-center gap-2 py-16 text-center"
    >
      <div class="rounded-full bg-surface-gray-2 p-3 text-ink-green-6">
        <span class="lucide-circle-check size-5" aria-hidden="true" />
      </div>
      <p class="text-base text-ink-gray-7">No rows — clean</p>
    </div>

    <!-- Table -->
    <div v-else class="flex-1 overflow-auto px-3 sm:px-5 pt-4">
      <ListView
        :columns="list_columns"
        :rows="list_rows"
        row-key="_row_idx"
        :options="{ selectable: false }"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, inject } from 'vue'
import { Badge, Button, ListView } from 'frappe-ui'

const props = defineProps({
  report_name: { type: String, required: true },
  description: { type: String, default: '' },
  default_filters: { type: Object, default: () => ({}) },
})

const call = inject('$call')

const columns = ref([])
const raw_rows = ref([])
const loading = ref(false)
const filters = ref({ ...props.default_filters })
let fetched_once = false

const rows = computed(() =>
  raw_rows.value.filter((row) => typeof row === 'object' && row !== null && !Array.isArray(row))
)

const row_count = computed(() => (fetched_once ? rows.value.length : null))

const list_columns = computed(() =>
  columns.value.map((col) => ({
    label: col.label,
    key: col.fieldname,
    width: col.width ? `${col.width}px` : undefined,
  }))
)

const list_rows = computed(() =>
  rows.value.map((row, idx) => {
    const formatted = { _row_idx: idx }
    for (const col of columns.value) {
      const raw_value = row[col.fieldname]
      if (col.fieldtype === 'Currency') {
        formatted[col.fieldname] = raw_value != null ? `£${Number(raw_value).toFixed(2)}` : '—'
      } else if (col.fieldtype === 'Check') {
        formatted[col.fieldname] = raw_value ? '✓' : '—'
      } else {
        formatted[col.fieldname] = raw_value ?? '—'
      }
    }
    return formatted
  })
)

async function fetch_report() {
  loading.value = true
  try {
    const result = await call('frappe.desk.query_report.run', {
      report_name: props.report_name,
      filters: filters.value,
      ignore_prepared_report: 1,
    })
    columns.value = result?.columns || []
    raw_rows.value = result?.result || []
    fetched_once = true
  } catch (e) {
    console.error('ReportPanel fetch failed', e)
  } finally {
    loading.value = false
  }
}

function open_in_desk() {
  window.open(`/app/query-report/${encodeURIComponent(props.report_name)}`, '_blank')
}

onMounted(() => {
  fetch_report()
})

defineExpose({ fetch_report, filters })
</script>

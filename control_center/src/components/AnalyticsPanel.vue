<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-base font-semibold text-ink-gray-9">Order analytics</h2>
      <TabButtons
        :buttons="period_buttons"
        v-model="selected_hours"
        @update:modelValue="on_period_change"
      />
    </div>

    <div v-if="analytics_data" class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <div class="text-2xl font-semibold text-ink-gray-9">{{ analytics_data?.cards?.orders_past_hour ?? 0 }}</div>
        <div class="text-sm text-ink-gray-5 mt-0.5">Orders (1h)</div>
      </div>
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <div class="text-2xl font-semibold text-ink-gray-9">{{ format_currency(analytics_data?.cards?.sales_past_hour) }}</div>
        <div class="text-sm text-ink-gray-5 mt-0.5">Sales (1h)</div>
      </div>
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <div class="text-2xl font-semibold text-ink-gray-9">{{ analytics_data?.cards?.orders_period ?? 0 }}</div>
        <div class="text-sm text-ink-gray-5 mt-0.5">Orders (period)</div>
      </div>
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <div class="text-2xl font-semibold text-ink-gray-9">{{ format_currency(analytics_data?.cards?.sales_period) }}</div>
        <div class="text-sm text-ink-gray-5 mt-0.5">Sales (period)</div>
      </div>
    </div>
    <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div
        v-for="i in 4"
        :key="i"
        class="bg-surface-gray-1 border border-outline-gray-1 rounded-md p-4 h-24"
      ></div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <h3 class="text-sm font-semibold text-ink-gray-7 mb-3">Order types</h3>
        <div ref="pie_chart_ref" class="h-48"></div>
      </div>
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <h3 class="text-sm font-semibold text-ink-gray-7 mb-3">Top categories</h3>
        <div ref="bar_chart_ref" class="h-48"></div>
      </div>
      <div class="bg-surface-base border border-outline-gray-1 rounded-md p-4">
        <h3 class="text-sm font-semibold text-ink-gray-7 mb-3">Hourly trend</h3>
        <div ref="line_chart_ref" class="h-48"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, inject, onMounted, onUnmounted, watch } from 'vue'
import { TabButtons } from 'frappe-ui'
import { Chart } from 'frappe-charts'

const call = inject('$call')

const analytics_data = ref(null)
const selected_hours = ref(24)

const pie_chart_ref = ref(null)
const bar_chart_ref = ref(null)
const line_chart_ref = ref(null)

let pie_chart_instance = null
let bar_chart_instance = null
let line_chart_instance = null

const period_buttons = [
  { label: '1h', value: 1 },
  { label: '24h', value: 24 },
  { label: '1w', value: 168 },
]

function format_currency(value) {
  if (value === null || value === undefined) return '£0.00'
  return '£' + Number(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function on_period_change(hours) {
  await load_analytics(hours)
}

async function load_analytics(hours) {
  try {
    const result = await call('navgold_custom.navgold_custom.amazon_pricing.control_center_api.get_order_analytics', { hours })
    analytics_data.value = result
  } catch (e) {
    console.error('get_order_analytics failed', e)
  }
}

function render_charts() {
  if (!analytics_data.value) return

  const pie_data = analytics_data.value?.order_type_pie || []
  if (pie_chart_ref.value && pie_data.length) {
    if (pie_chart_instance) pie_chart_instance.destroy()
    pie_chart_ref.value.innerHTML = ''
    pie_chart_instance = new Chart(pie_chart_ref.value, {
      type: 'pie',
      data: {
        labels: pie_data.map((d) => d.label),
        datasets: [{ values: pie_data.map((d) => d.value) }],
      },
      colors: ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6'],
      height: 180,
    })
  }

  const top_categories = analytics_data.value?.top_categories || []
  if (bar_chart_ref.value && top_categories.length) {
    if (bar_chart_instance) bar_chart_instance.destroy()
    bar_chart_ref.value.innerHTML = ''
    bar_chart_instance = new Chart(bar_chart_ref.value, {
      type: 'bar',
      data: {
        labels: top_categories.map((d) => d.label),
        datasets: [{ name: 'Qty', values: top_categories.map((d) => d.qty) }],
      },
      colors: ['#3b82f6'],
      height: 180,
    })
  }

  const hourly_series = analytics_data.value?.hourly_series || []
  if (line_chart_ref.value && hourly_series.length) {
    if (line_chart_instance) line_chart_instance.destroy()
    line_chart_ref.value.innerHTML = ''
    line_chart_instance = new Chart(line_chart_ref.value, {
      type: 'line',
      data: {
        labels: hourly_series.map((d) => d.ts),
        datasets: [
          { name: 'Orders', values: hourly_series.map((d) => d.orders) },
          { name: 'Sales', values: hourly_series.map((d) => d.sales) },
        ],
      },
      colors: ['#10b981', '#f59e0b'],
      height: 180,
    })
  }
}

watch(analytics_data, () => {
  render_charts()
}, { deep: true })

onMounted(() => {
  load_analytics(selected_hours.value)
})

onUnmounted(() => {
  if (pie_chart_instance) pie_chart_instance.destroy()
  if (bar_chart_instance) bar_chart_instance.destroy()
  if (line_chart_instance) line_chart_instance.destroy()
})
</script>

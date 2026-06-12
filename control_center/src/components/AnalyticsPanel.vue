<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-white">Order Analytics</h2>
      <div class="flex gap-2">
        <button
          v-for="period in periods"
          :key="period.label"
          @click="select_period(period)"
          :class="selected_hours === period.hours ? 'bg-blue-600 text-white px-4 py-2 rounded text-sm font-semibold' : 'bg-gray-800 text-gray-400 px-4 py-2 rounded text-sm font-semibold'"
        >
          {{ period.label }}
        </button>
      </div>
    </div>

    <div v-if="analytics_data" class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <div class="text-3xl font-black text-white">{{ analytics_data?.cards?.orders_past_hour ?? 0 }}</div>
        <div class="text-xs text-gray-400 uppercase tracking-wide mt-1">Orders (1h)</div>
      </div>
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <div class="text-3xl font-black text-white">{{ format_currency(analytics_data?.cards?.sales_past_hour) }}</div>
        <div class="text-xs text-gray-400 uppercase tracking-wide mt-1">Sales (1h)</div>
      </div>
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <div class="text-3xl font-black text-white">{{ analytics_data?.cards?.orders_period ?? 0 }}</div>
        <div class="text-xs text-gray-400 uppercase tracking-wide mt-1">Orders (Period)</div>
      </div>
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <div class="text-3xl font-black text-white">{{ format_currency(analytics_data?.cards?.sales_period) }}</div>
        <div class="text-xs text-gray-400 uppercase tracking-wide mt-1">Sales (Period)</div>
      </div>
    </div>
    <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div v-for="i in 4" :key="i" class="bg-gray-900 rounded-xl p-4 border border-gray-700 h-24 animate-pulse"></div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <h3 class="text-sm font-semibold text-gray-300 mb-3">Order Types</h3>
        <div ref="pie_chart_ref" class="h-48"></div>
      </div>
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <h3 class="text-sm font-semibold text-gray-300 mb-3">Top Categories</h3>
        <div ref="bar_chart_ref" class="h-48"></div>
      </div>
      <div class="bg-gray-900 rounded-xl p-4 border border-gray-700">
        <h3 class="text-sm font-semibold text-gray-300 mb-3">Hourly Trend</h3>
        <div ref="line_chart_ref" class="h-48"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, inject, onMounted, onUnmounted, watch } from 'vue'
import { Chart } from 'frappe-charts'

const props = defineProps({
  analytics: { type: Object, default: null }
})

const call = inject('$call')

const analytics_data = ref(props.analytics)
const selected_hours = ref(24)

const pie_chart_ref = ref(null)
const bar_chart_ref = ref(null)
const line_chart_ref = ref(null)

let pie_chart_instance = null
let bar_chart_instance = null
let line_chart_instance = null

const periods = [
  { label: '1h', hours: 1 },
  { label: '24h', hours: 24 },
  { label: '1w', hours: 168 },
]

function format_currency(value) {
  if (value === null || value === undefined) return '£0.00'
  return '£' + Number(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function select_period(period) {
  selected_hours.value = period.hours
  await load_analytics(period.hours)
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
        labels: pie_data.map(d => d.label),
        datasets: [{ values: pie_data.map(d => d.value) }]
      },
      colors: ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6'],
      height: 180
    })
  }

  const top_categories = analytics_data.value?.top_categories || []
  if (bar_chart_ref.value && top_categories.length) {
    if (bar_chart_instance) bar_chart_instance.destroy()
    bar_chart_ref.value.innerHTML = ''
    bar_chart_instance = new Chart(bar_chart_ref.value, {
      type: 'bar',
      data: {
        labels: top_categories.map(d => d.label),
        datasets: [{ name: 'Qty', values: top_categories.map(d => d.qty) }]
      },
      colors: ['#3b82f6'],
      height: 180
    })
  }

  const hourly_series = analytics_data.value?.hourly_series || []
  if (line_chart_ref.value && hourly_series.length) {
    if (line_chart_instance) line_chart_instance.destroy()
    line_chart_ref.value.innerHTML = ''
    line_chart_instance = new Chart(line_chart_ref.value, {
      type: 'line',
      data: {
        labels: hourly_series.map(d => d.ts),
        datasets: [
          { name: 'Orders', values: hourly_series.map(d => d.orders) },
          { name: 'Sales', values: hourly_series.map(d => d.sales) }
        ]
      },
      colors: ['#10b981', '#f59e0b'],
      height: 180
    })
  }
}

watch(analytics_data, () => {
  render_charts()
}, { deep: true })

watch(() => props.analytics, (new_val) => {
  if (new_val && !analytics_data.value) {
    analytics_data.value = new_val
  }
})

onMounted(() => {
  if (!analytics_data.value) {
    load_analytics(selected_hours.value)
  } else {
    render_charts()
  }
})

onUnmounted(() => {
  if (pie_chart_instance) pie_chart_instance.destroy()
  if (bar_chart_instance) bar_chart_instance.destroy()
  if (line_chart_instance) line_chart_instance.destroy()
})
</script>

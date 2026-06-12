<template>
  <nav class="flex w-48 shrink-0 flex-col border-r border-outline-gray-1 bg-surface-sidebar py-3">
    <div class="px-3 pb-3 mb-1 border-b border-outline-gray-1">
      <span class="text-sm font-semibold text-ink-gray-9">Control Center</span>
    </div>
    <div class="flex-1 overflow-y-auto px-1.5 space-y-0.5">
      <router-link
        v-for="item in nav_items"
        :key="item.route"
        :to="item.route"
        custom
        v-slot="{ isActive, navigate }"
      >
        <button
          @click="navigate"
          :class="[
            'flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-ink-gray-7 hover:bg-surface-gray-2',
            isActive ? 'bg-surface-gray-2 text-ink-gray-9 font-medium' : '',
          ]"
        >
          <span :class="`lucide-${item.icon} size-4`" aria-hidden="true" />
          {{ item.label }}
        </button>
      </router-link>
    </div>
  </nav>
</template>

<script setup>
const nav_items = [
  { label: 'Overview', route: '/', icon: 'layout-dashboard' },
  { label: 'Data Readiness', route: '/data-readiness', icon: 'circle-check-big' },
  { label: 'Pricing Push', route: '/pricing-push', icon: 'send' },
  { label: 'Below Min Sales', route: '/below-min-sales', icon: 'trending-down' },
  { label: 'Order Sync', route: '/order-sync', icon: 'refresh-cw' },
  { label: 'Supplier Readiness', route: '/supplier-readiness', icon: 'truck' },
  { label: 'Push Readiness', route: '/push-readiness', icon: 'rocket' },
  { label: 'Emergency', route: '/emergency', icon: 'shield-alert' },
]
</script>

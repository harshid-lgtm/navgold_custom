<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      report_name="Amazon Order Sync Health"
      description="Recent Amazon order sync attempts and any failures."
    />
  </div>
</template>

<script setup>
import ReportPanel from '../../components/ReportPanel.vue'
</script>

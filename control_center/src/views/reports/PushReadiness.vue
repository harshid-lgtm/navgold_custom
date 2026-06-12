<template>
  <div class="flex h-full flex-col">
    <ReportPanel
      report_name="Amazon Pricing Push Readiness"
      description="SKUs that are not ready to be pushed to Amazon pricing."
    />
  </div>
</template>

<script setup>
import ReportPanel from '../../components/ReportPanel.vue'
</script>

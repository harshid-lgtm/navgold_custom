<template>
  <div class="flex flex-wrap gap-1.5">
    <Badge
      v-for="flag_def in flag_definitions"
      :key="flag_def.key"
      :label="flag_def.label"
      :theme="flags[flag_def.key] ? 'green' : 'gray'"
      variant="subtle"
      size="sm"
    />
  </div>
</template>

<script setup>
import { Badge } from 'frappe-ui'
import { FLAG_DEFINITIONS } from '../constants'

defineProps({
  flags: { type: Object, default: () => ({}) },
})

const flag_definitions = FLAG_DEFINITIONS
</script>

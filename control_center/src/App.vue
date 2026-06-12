<template>
  <div class="min-h-screen bg-gray-950 text-white">
    <router-view />
  </div>
</template>
<script>
export default { inject: ['$auth'] }
</script>

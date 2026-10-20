<script setup>
defineProps({ status: String })
</script>

<template>
    <form :action="route('login')">{{ status }}</form>
</template>

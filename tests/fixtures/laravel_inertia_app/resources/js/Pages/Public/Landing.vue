<script setup>
import { router } from '@inertiajs/vue3'

function goHome() {
    router.get(route('home'))
}
</script>

<template>
    <button @click="goHome">home</button>
</template>

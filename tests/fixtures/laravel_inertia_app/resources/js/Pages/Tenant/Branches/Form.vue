<script setup lang="ts">
interface Props {
    branch?: object | null
    mode: string
}

const props = withDefaults(defineProps<Props>(), { branch: null })
</script>

<template>
    <form :action="route('tenant.branches.store')">{{ props.mode }}</form>
</template>

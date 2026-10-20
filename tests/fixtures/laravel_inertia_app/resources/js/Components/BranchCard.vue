<script setup>
import { Link } from '@inertiajs/vue3'

const props = defineProps({ branch: Object })

const showUrl = () => route('tenant.branches.show', props.branch.id)
</script>

<template>
    <Link :href="route('tenant.branches.edit', branch.id)">{{ branch.name }}</Link>
</template>

<script setup>
import { router } from '@inertiajs/vue3'
import { Alert } from '@/Components/ui/alert'
import BranchCard from '@/Components/BranchCard.vue'
import { notify } from '@kit/notify'

const props = defineProps({
    branches: Array,
    filters: { type: Object, default: () => ({}) },
    stale_prop: String,
    auth: Object,
})

function openBranch(id) {
    router.visit(route('tenant.branches.show', id))
}

const create = () => router.get(route('tenant.branches.create'))
const label = route('no.such.route')
const dynamicName = (n) => route(`tenant.branches.${n}`)
</script>

<template>
    <div>
        <Alert />
        <BranchCard v-for="b in branches" :key="b.id" :branch="b" />
        <a :href="route('tenant.branches.index')" :class="{ on: route().current('tenant.branches.*') }">All</a>
        <a :href="route('home')" :aria-current="route().current('home')">Home</a>
        <!-- <a :href="route('tenant.branches.edit', 1)">commented</a> -->
    </div>
</template>

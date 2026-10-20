<div>
    <span>{{ $count }}</span>
    <button wire:click="increment">+</button>
    <button wire:click.prevent="add({{ $n }})">add</button>
    <button wire:click="add(5)">add five</button>
    <button wire:click="$refresh">refresh</button>
    <button wire:click="$set('count', 0)">reset</button>
    <button wire:click="notAMethod">ghost</button>
    <button wire:click="{{ $dynamicAction }}">dynamic</button>
    <button x-on:click="$wire.save()">alpine</button>
    <input wire:model.live.debounce.300ms="count">
    <input wire:model="form.title">
    <form wire:submit="save"></form>
    <x-button wire:click="increment">via component</x-button>
</div>

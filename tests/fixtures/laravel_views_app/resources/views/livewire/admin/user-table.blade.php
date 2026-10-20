<div>
    <input wire:model="search">
    <button wire:click="delete({{ $user->id }})">delete</button>
    <button wire:click="missing">missing</button>
</div>

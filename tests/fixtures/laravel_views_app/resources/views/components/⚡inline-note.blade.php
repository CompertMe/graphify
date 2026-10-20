<?php

use Livewire\Component;

new class extends Component
{
    public string $note = '';

    public function store(): void
    {
    }

    public function clear(): void
    {
    }
};
?>

<div>
    <textarea wire:model="note"></textarea>
    <button wire:click="store">save</button>
    <button wire:click="clear()">clear</button>
    <button wire:click="nope">nope</button>
</div>

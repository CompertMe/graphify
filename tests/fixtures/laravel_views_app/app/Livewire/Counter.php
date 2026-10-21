<?php

namespace App\Livewire;

use Livewire\Component;

class Counter extends Component
{
    public int $count = 0;

    public function increment(): void
    {
        $this->count++;
    }

    public function add(int $n): void
    {
        $this->count += $n;
    }

    public function save(): void
    {
    }

    public function render()
    {
        return view('livewire.counter')->layout('layouts.app');
    }
}

<?php

namespace App\Livewire;

use Livewire\Component;

class TwinA extends Component
{
    public function ping(): void
    {
    }

    public function render()
    {
        return view('livewire.twin');
    }
}

<?php

namespace App\Livewire;

use Livewire\Component;

class Dyn extends Component
{
    public string $which = 'counter';

    public function render()
    {
        return view('livewire.' . $this->which);
    }
}

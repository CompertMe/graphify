<?php

namespace App\Livewire;

use Livewire\Component;

class BranchForm extends Component
{
    protected $rules = [
        'client_id' => 'required|exists:clients,id',
    ];

    public function save(): void
    {
        $this->validate();
    }
}

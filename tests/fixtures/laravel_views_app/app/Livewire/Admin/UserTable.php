<?php

namespace App\Livewire\Admin;

use Livewire\Component;

class UserTable extends Component
{
    public string $search = '';

    public function delete(int $id): void
    {
    }
}

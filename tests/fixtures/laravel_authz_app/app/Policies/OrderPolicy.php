<?php

namespace App\Policies;

class OrderPolicy
{
    public function view($user): bool
    {
        return true;
    }
}

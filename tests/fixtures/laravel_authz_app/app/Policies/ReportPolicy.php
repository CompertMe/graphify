<?php

namespace App\Policies;

class ReportPolicy
{
    public function view($user): bool
    {
        return true;
    }

    public function create($user): bool
    {
        return true;
    }
}

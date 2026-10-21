<?php

namespace App\Domain\Billing;

class OrderPolicy
{
    public function view($user): bool
    {
        return true;
    }

    public function refund($user): bool
    {
        return true;
    }
}

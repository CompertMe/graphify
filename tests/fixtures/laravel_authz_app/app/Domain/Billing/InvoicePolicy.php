<?php

namespace App\Domain\Billing;

class InvoicePolicy
{
    public function view($user): bool
    {
        return true;
    }
}

<?php

namespace App\Services\Impl;

use App\Services\Contracts\Payment;

class Gateway implements Payment
{
    public function charge(int $cents): bool
    {
        return true;
    }
}

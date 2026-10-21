<?php

namespace App\Services\Contracts;

interface Payment
{
    public function charge(int $cents): bool;
}

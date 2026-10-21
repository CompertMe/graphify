<?php

namespace App\Services\Contracts;

interface Clock
{
    public function now(): int;
}

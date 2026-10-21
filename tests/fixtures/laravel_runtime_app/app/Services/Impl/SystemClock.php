<?php

namespace App\Services\Impl;

use App\Services\Contracts\Clock;

class SystemClock implements Clock
{
    public function now(): int
    {
        return time();
    }
}

<?php

namespace App\Facades;

use App\Services\Contracts\Payment;
use Illuminate\Support\Facades\Facade;

class Pay extends Facade
{
    protected static function getFacadeAccessor()
    {
        return Payment::class;
    }
}

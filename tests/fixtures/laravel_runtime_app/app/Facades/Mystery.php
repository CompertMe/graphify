<?php

namespace App\Facades;

use Illuminate\Support\Facades\Facade;

class Mystery extends Facade
{
    protected static function getFacadeAccessor(): string
    {
        return 'mystery.' . config('x');
    }
}

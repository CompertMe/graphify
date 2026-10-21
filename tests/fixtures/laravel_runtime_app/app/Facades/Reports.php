<?php

namespace App\Facades;

use Illuminate\Support\Facades\Facade;

class Reports extends Facade
{
    protected static function getFacadeAccessor(): string
    {
        return 'reports';
    }
}

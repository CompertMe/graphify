<?php

namespace App\Http;

use App\Http\Middleware\KernelMiddleware;
use App\Http\Middleware\LegacyGroupMiddleware;
use Illuminate\Foundation\Http\Kernel as HttpKernel;

class Kernel extends HttpKernel
{
    protected $middlewareGroups = [
        'legacy' => [
            LegacyGroupMiddleware::class,
            \Illuminate\Session\Middleware\StartSession::class,
        ],
    ];

    protected $middlewareAliases = [
        'kernel.mw' => KernelMiddleware::class,
        'auth' => \Illuminate\Auth\Middleware\Authenticate::class,
    ];
}

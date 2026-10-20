<?php

namespace App\Http\Middleware;

use Closure;

class KernelMiddleware
{
    public function handle($request, Closure $next)
    {
        return $next($request);
    }
}

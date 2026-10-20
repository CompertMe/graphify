<?php

namespace App\Http\Middleware;

use Closure;

class ExplicitMiddleware
{
    public function handle($request, Closure $next)
    {
        return $next($request);
    }
}

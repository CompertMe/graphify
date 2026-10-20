<?php

namespace App\Http\Middleware;

use Closure;

class LegacyGroupMiddleware
{
    public function handle($request, Closure $next)
    {
        return $next($request);
    }
}

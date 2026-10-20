<?php

namespace App\Http\Middleware;

use Closure;

class GlobalStamp
{
    public function handle($request, Closure $next)
    {
        return $next($request);
    }
}

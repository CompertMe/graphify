<?php

use App\Http\Middleware\ApiLog;
use App\Http\Middleware\EnsureAdmin;
use App\Http\Middleware\GlobalStamp;
use App\Http\Middleware\Locale;
use App\Http\Middleware\TrackVisits;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Support\Facades\Route;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        then: function () {
            Route::middleware('web')->prefix('extra')->name('extra.')->group(base_path('routes/extra.php'));
        },
    )
    ->withMiddleware(function (Middleware $middleware): void {
        $middleware->alias([
            'admin' => EnsureAdmin::class,
            'visits' => \App\Http\Middleware\TrackVisits::class,
            'throttle' => \Illuminate\Routing\Middleware\ThrottleRequests::class,
        ]);
        $middleware->web(append: [Locale::class]);
        $middleware->api(prepend: [ApiLog::class]);
        $middleware->group('tracked', [TrackVisits::class, \Illuminate\Session\Middleware\StartSession::class]);
        $middleware->append(GlobalStamp::class);
    })
    ->create();

<?php

namespace App\Providers;

use Illuminate\Support\ServiceProvider;
use Inertia\Inertia;
use Laravel\Fortify\Fortify;

class FortifyServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        Fortify::loginView(fn () => Inertia::render('Auth/Login', ['status' => session('status')]));
        Fortify::registerView(function () {
            return Inertia::render('Auth/Register');
        });
    }
}

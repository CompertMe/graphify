<?php

namespace App\Services;

use App\Support\Config;
use App\Support\Gate;

// `Config` and `Gate` here are the project's own classes, not Laravel's facades.
class NotFacades
{
    public function run($user)
    {
        Config::get('app.name');
        Gate::allows('create-branches');
        Route::get('/x', fn () => 1)->can('close-books');
    }
}

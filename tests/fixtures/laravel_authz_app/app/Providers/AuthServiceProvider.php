<?php

namespace App\Providers;

use App\Domain\Billing\OrderPolicy;
use App\Gates\LegacyGate;
use App\Models\Order;
use App\Models\Report;
use App\Policies\ReportPolicy;
use Illuminate\Support\Facades\Gate;
use Illuminate\Support\ServiceProvider;

class AuthServiceProvider extends ServiceProvider
{
    protected $policies = [
        Order::class => OrderPolicy::class,
    ];

    public function boot(): void
    {
        Gate::define('create-branches', fn ($user) => $user->isAdmin());
        Gate::define('close-books', function ($user) {
            return $user->isAdmin();
        });
        Gate::define('manage-legacy', [LegacyGate::class, 'manage']);
        Gate::define('legacy-check', 'App\Gates\LegacyGate@check');
        Gate::define('invokable-gate', LegacyGate::class);
        Gate::define('no-such-handler', [LegacyGate::class, 'missing']);
        Gate::policy(Report::class, ReportPolicy::class);
        Gate::resource('reports', ReportPolicy::class);

        foreach (['a', 'b'] as $name) {
            Gate::define($name, fn ($user) => false);
        }
    }
}

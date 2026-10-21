<?php

namespace App\Providers;

use App\Events\TenantCreated;
use App\Jobs\ChildJob;
use App\Jobs\ProcessReport;
use App\Listeners\ShipListener;
use Illuminate\Support\ServiceProvider;
use Stancl\JobPipeline\JobPipeline;

class TenancyServiceProvider extends ServiceProvider
{
    public function events(): array
    {
        return [
            TenantCreated::class => [
                JobPipeline::make([
                    ProcessReport::class,
                    ChildJob::class,
                ])->send(function (TenantCreated $event) {
                    return $event->tenant;
                })->shouldBeQueued(false)->toListener(),
                ShipListener::class,
            ],
        ];
    }
}

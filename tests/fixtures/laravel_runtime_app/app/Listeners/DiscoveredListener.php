<?php

namespace App\Listeners;

use App\Events\Discovered;
use App\Events\OrderShipped;

class DiscoveredListener
{
    public function handle(Discovered $event): void
    {
    }

    public function handleShipped(OrderShipped|Discovered $event): void
    {
    }

    public function helper(Discovered $event): void
    {
    }
}

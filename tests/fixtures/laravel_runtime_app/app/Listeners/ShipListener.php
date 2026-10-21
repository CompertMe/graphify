<?php

namespace App\Listeners;

use App\Events\OrderShipped;

class ShipListener
{
    public function onShipped(OrderShipped $event): void
    {
    }
}

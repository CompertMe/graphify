<?php

namespace App\Events;

use Illuminate\Foundation\Events\Dispatchable;

class OrderShipped
{
    use Dispatchable;

    public function __construct(public int $orderId = 0)
    {
    }
}

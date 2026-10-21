<?php

namespace App\Listeners;

use App\Events\InvoicePaid;
use Illuminate\Events\Attributes\ListensTo;

#[ListensTo(InvoicePaid::class)]
class AttributeListener
{
    public function __invoke($event): void
    {
    }
}

<?php

namespace App\Subscribers;

use App\Events\InvoicePaid;
use App\Events\TenantCreated;
use Illuminate\Events\Dispatcher;

class AuditSubscriber
{
    public function onPaid(InvoicePaid $event): void
    {
    }

    public function onTenant(TenantCreated $event): void
    {
    }

    public function subscribe(Dispatcher $events): void
    {
        $events->listen(InvoicePaid::class, [self::class, 'onPaid']);
        $events->listen(TenantCreated::class, 'App\Subscribers\AuditSubscriber@onTenant');
    }
}

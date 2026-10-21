<?php

namespace App\Subscribers;

use App\Events\ClosureEvent;

class UntypedSubscriber
{
    public function onClosure(ClosureEvent $event): void
    {
    }

    public function subscribe($events)
    {
        return [
            ClosureEvent::class => 'onClosure',
        ];
    }
}

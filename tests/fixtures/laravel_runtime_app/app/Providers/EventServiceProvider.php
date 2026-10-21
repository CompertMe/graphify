<?php

namespace App\Providers;

use App\Events\UserRegistered;
use App\Listeners\NotifyAdmins;
use App\Listeners\SendWelcomeEmail;
use App\Subscribers\AuditSubscriber;
use App\Subscribers\UntypedSubscriber;
use Illuminate\Foundation\Support\Providers\EventServiceProvider as ServiceProvider;

class EventServiceProvider extends ServiceProvider
{
    protected $listen = [
        UserRegistered::class => [
            SendWelcomeEmail::class,
            [NotifyAdmins::class, 'onFailure'],
            'App\Listeners\LogLogin@handle',
        ],
    ];

    protected $subscribe = [
        AuditSubscriber::class,
        UntypedSubscriber::class,
    ];
}

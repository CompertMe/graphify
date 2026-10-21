<?php

namespace App\Listeners;

use App\Events\UserRegistered;

class NotifyAdmins
{
    public function handle(UserRegistered $event): void
    {
    }

    public function onFailure(UserRegistered $event): void
    {
    }
}

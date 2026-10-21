<?php

namespace App\Notifications;

use Illuminate\Notifications\Notification;

class InvoiceNotification extends Notification
{
    public function via($notifiable): array
    {
        return ['mail'];
    }
}

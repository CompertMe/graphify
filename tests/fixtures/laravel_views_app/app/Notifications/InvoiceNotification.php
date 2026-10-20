<?php

namespace App\Notifications;

use Illuminate\Notifications\Messages\MailMessage;
use Illuminate\Notifications\Notification;

class InvoiceNotification extends Notification
{
    public function toMail($notifiable): MailMessage
    {
        return (new MailMessage)->markdown('emails.digest', ['n' => 1]);
    }
}

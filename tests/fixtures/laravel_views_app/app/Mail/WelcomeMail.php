<?php

namespace App\Mail;

use Illuminate\Mail\Mailable;
use Illuminate\Mail\Mailables\Content;

class WelcomeMail extends Mailable
{
    public function content(): Content
    {
        return new Content(
            view: 'emails.welcome',
            markdown: 'emails.digest',
        );
    }
}

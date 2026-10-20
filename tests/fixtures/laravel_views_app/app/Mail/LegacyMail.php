<?php

namespace App\Mail;

use Illuminate\Mail\Mailable;

class LegacyMail extends Mailable
{
    public function build()
    {
        return $this->view('emails.welcome')->text('emails.plain');
    }
}

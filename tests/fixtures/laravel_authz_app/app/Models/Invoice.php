<?php

namespace App\Models;

use App\Domain\Billing\InvoicePolicy;
use Illuminate\Database\Eloquent\Attributes\UsePolicy;

#[UsePolicy(InvoicePolicy::class)]
class Invoice
{
}

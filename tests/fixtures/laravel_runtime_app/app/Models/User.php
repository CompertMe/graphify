<?php

namespace App\Models;

use App\Observers\AuditObserver;
use Illuminate\Database\Eloquent\Model;

class User extends Model
{
    protected static function booted(): void
    {
        static::observe(AuditObserver::class);
    }
}

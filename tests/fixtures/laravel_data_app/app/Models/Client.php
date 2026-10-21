<?php

namespace App\Models;

use App\Enums\ClientStatus;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\SoftDeletes;

class Client extends Model
{
    use HasFactory, SoftDeletes;

    protected $casts = [
        'status' => ClientStatus::class,
        'meta' => 'array',
        'joined_at' => 'datetime',
    ];

    public function branches(): HasMany
    {
        return $this->hasMany(Branch::class);
    }

    public function employees()
    {
        return $this->hasManyThrough(Employee::class, Branch::class, 'client_id', 'branch_id');
    }

    public function comments()
    {
        return $this->morphMany(Comment::class, 'commentable');
    }

    public function contact()
    {
        return $this->hasOne(Person::class, 'client_id');
    }

    public function headOfficeStaff()
    {
        return $this->hasOneThrough(Employee::class, Branch::class);
    }

    public function dynamic(string $related)
    {
        return $this->hasMany($related);
    }

    public static function fromEmail(string $email): ?self
    {
        return static::where('email', $email)->first();
    }
}

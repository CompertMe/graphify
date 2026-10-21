<?php

namespace App\Models;

use App\Enums\BranchType;
use Illuminate\Database\Eloquent\Model;

class Branch extends Model
{
    protected function casts(): array
    {
        return [
            'opened_at' => 'datetime',
            'type' => BranchType::class,
        ];
    }

    public function client()
    {
        return $this->belongsTo(Client::class, 'client_id')->withDefault();
    }

    public function employees()
    {
        return $this->hasMany(Employee::class, foreignKey: 'branch_id');
    }

    public function tags()
    {
        return $this->belongsToMany(Tag::class, 'branch_tag', 'branch_id', 'tag_id')->using(BranchTag::class);
    }

    public function manager()
    {
        return $this->belongsTo(User::class, 'manager_id');
    }

    public function owner()
    {
        return $this->morphTo();
    }

    public function scopeActive($query)
    {
        return $query->where('active', true);
    }

    public static function fromCode(string $code): ?self
    {
        return self::where('code', $code)->first();
    }
}

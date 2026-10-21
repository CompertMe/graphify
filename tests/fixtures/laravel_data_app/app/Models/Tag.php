<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Tag extends Model
{
    public function branches()
    {
        return $this->morphedByMany(Branch::class, 'taggable');
    }
}

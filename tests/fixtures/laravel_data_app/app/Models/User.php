<?php

namespace App\Models;

use Illuminate\Foundation\Auth\User as Authenticatable;

class User extends Authenticatable
{
    public function branches()
    {
        return $this->hasMany(Branch::class, 'manager_id');
    }
}

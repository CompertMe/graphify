<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Employee extends Model
{
    protected $table = 'staff_members';

    public function branch()
    {
        return $this->belongsTo(Branch::class);
    }

    public function mentor()
    {
        return $this->belongsTo(Employee::class, 'mentor_id');
    }
}

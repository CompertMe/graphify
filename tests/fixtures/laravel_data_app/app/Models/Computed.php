<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Computed extends Model
{
    public function getTable()
    {
        return config('app.computed_table');
    }
}

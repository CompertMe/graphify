<?php

namespace App\Http\Controllers;

use App\Support\Branch;

class SupportController
{
    public function lookup()
    {
        // `Branch` is the plain App\Support class here, never the Eloquent model of the same name.
        return Branch::where('code', 'x');
    }

    public function sameNamespace()
    {
        // No import: `Client` is App\Http\Controllers\Client, which does not exist - no edge.
        return Client::all();
    }
}

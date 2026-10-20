<?php

namespace App\Http\Controllers;

class InvokableController extends Controller
{
    public function __invoke()
    {
        return 'ok';
    }
}

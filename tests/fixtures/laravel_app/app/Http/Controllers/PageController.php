<?php

namespace App\Http\Controllers;

use App\Http\Controllers\Concerns\AuditsActions;

class PageController extends Controller
{
    use AuditsActions;

    public function home()
    {
        return 'home';
    }

    public function index()
    {
        return 'pages';
    }

    public function show($page)
    {
        return $page;
    }

    public function multi()
    {
        return 'multi';
    }
}

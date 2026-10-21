<?php

namespace App\Http\Controllers;

use Illuminate\Support\Facades\View;
use Barryvdh\DomPDF\Facade\Pdf;

class ReportController
{
    public function show()
    {
        return view('reports.show', ['x' => 1]);
    }

    public function viaFacade()
    {
        return View::make('reports.partials.summary');
    }

    public function viaResponse()
    {
        return response()->view('reports.partials.chart', []);
    }

    public function namespaced()
    {
        return view('blog::index');
    }

    public function themed()
    {
        return view(theme_view('auth.login', 'guest'));
    }

    public function pdf()
    {
        return Pdf::loadView('reports.pdf', []);
    }

    public function viaFactory()
    {
        return view()->make('reports.partials.chart');
    }

    public function missing()
    {
        // no such view in the corpus
        return view('reports.nope');
    }

    public function dynamic(string $name)
    {
        $prefix = 'reports.';

        return view($prefix . $name) ?: view("reports.$name") ?: view($name);
    }

    public function ambiguous()
    {
        return view('twin::index') ?: view('dyn::index');
    }

    public function closure()
    {
        $render = fn () => view('reports.partials.chart');

        return $render();
    }
}

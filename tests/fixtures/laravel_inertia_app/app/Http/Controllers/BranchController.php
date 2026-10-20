<?php

namespace App\Http\Controllers;

use Inertia\Inertia;

class BranchController
{
    public function index()
    {
        return Inertia::render('Tenant/Branches/Index', [
            'branches' => [],
            'filters' => request()->all(),
            'orphan_prop' => 1,
        ]);
    }

    public function create()
    {
        $branch = null;

        return inertia('Tenant/Branches/Form', compact('branch'))->with('mode', 'create');
    }

    public function show($branch)
    {
        $props = ['branch' => $branch];

        return Inertia::render('Tenant/Branches/Show', $props);
    }

    public function store()
    {
        return to_route('tenant.branches.index');
    }

    public function edit($branch)
    {
        return Inertia::render('Tenant/Branches/Nope', ['branch' => $branch]);
    }

    public function dynamic(string $page)
    {
        Inertia::render($page);
        Inertia::render('Tenant/' . $page);

        return redirect()->route('tenant.branches.show', ['branch' => 1])->with('ok', true);
    }

    public function unknown()
    {
        return redirect()->route('no.such.route');
    }
}

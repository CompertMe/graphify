<?php

namespace App\Http\Controllers;

use App\Enums\PermissionEnum;
use App\Models\Branch;
use App\Models\Contact;
use App\Models\Invoice;
use App\Models\Order;
use Illuminate\Support\Facades\Gate;

class ContactController
{
    public function index()
    {
        $this->authorize('viewAny', Contact::class);
        return view('contacts.index');
    }

    public function update(Contact $contact)
    {
        Gate::authorize('update', $contact);
    }

    public function show(Order $order, Invoice $invoice, Branch $branch)
    {
        $this->authorize('view', $order);
        $this->authorize('view', $invoice);
        $this->authorize('view', $branch);      // no policy for Branch: nothing
        $this->authorize('view', $unknown);     // untyped variable: nothing
    }

    public function branches($user)
    {
        if (Gate::allows('create-branches')) {
            return 1;
        }
        if ($user->cannot('close-books')) {
            return 2;
        }
        if (Gate::any(['manage-legacy', 'legacy-check'])) {
            return 3;
        }
        if (auth()->user()?->can(PermissionEnum::ARCHIVE_ROLES->value)) {
            return 4;
        }
        if ($user->can('reports.view')) {
            return 5;
        }
        $user->can('undefined-ability');
        Gate::check($this->computed());
        Gate::allows('prefix-' . $this->suffix());
    }

    public function edit($user)
    {
        return $user->can(PermissionEnum::EDIT_CONTACTS->value) && $user->can(PermissionEnum::VIEW_CONTACTS);
    }
}

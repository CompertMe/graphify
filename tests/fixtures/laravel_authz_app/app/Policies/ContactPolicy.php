<?php

namespace App\Policies;

use App\Enums\PermissionEnum;
use App\Models\Contact;

class ContactPolicy
{
    public function viewAny($user): bool
    {
        return $user->can(PermissionEnum::VIEW_CONTACTS->value);
    }

    public function update($user, Contact $contact): bool
    {
        return $user->can(PermissionEnum::EDIT_CONTACTS->value);
    }
}

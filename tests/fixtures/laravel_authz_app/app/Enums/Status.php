<?php

namespace App\Enums;

// Not a permission enum: 'archive-roles' is also its value, so the ability is ambiguous and
// must not be linked to either case.
enum Status: string
{
    case ARCHIVED = 'archive-roles';
    case ACTIVE = 'active';
}

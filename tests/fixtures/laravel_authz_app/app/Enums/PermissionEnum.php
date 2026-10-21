<?php

namespace App\Enums;

enum PermissionEnum: string
{
    case VIEW_CONTACTS = 'view-contacts';
    case EDIT_CONTACTS = 'edit-contacts';
    case CREATE_BRANCHES = 'create-branches';
    case ARCHIVE_ROLES = 'archive-roles';
    case UNUSED = 'never-checked';
}

<?php

namespace App\Filament\Resources\Shared;

use Filament\Resources\RelationManagers\RelationManager;

class AuditRelationManager extends RelationManager
{
    protected static string $relationship = 'audits';
}

<?php

namespace App\Filament\Resources\BranchResource\RelationManagers;

use Filament\Resources\RelationManagers\RelationManager;

class EmployeesRelationManager extends RelationManager
{
    protected static string $relationship = 'employees';
}

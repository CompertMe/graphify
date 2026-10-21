<?php

namespace App\Filament\Resources;

use App\Filament\Resources\BranchResource\Pages;
use App\Filament\Resources\BranchResource\RelationManagers;
use App\Models\Branch;
use Filament\Resources\Resource;
use Filament\Resources\RelationManagers\RelationGroup;

class BranchResource extends Resource
{
    protected static ?string $model = Branch::class;

    public static function getRelations(): array
    {
        return [
            RelationManagers\EmployeesRelationManager::class,
            RelationGroup::make('Taxonomy', [
                RelationManagers\TagsRelationManager::class,
            ]),
            \App\Filament\Resources\Shared\AuditRelationManager::class,
            RelationManagers\MissingRelationManager::class,
        ];
    }

    public static function getPages(): array
    {
        return [
            'index' => Pages\ListBranches::route('/'),
            'create' => Pages\CreateBranch::route('/create'),
            'edit' => Pages\EditBranch::route('/{record}/edit'),
            'ghost' => Pages\NoSuchPage::route('/ghost'),
        ];
    }
}

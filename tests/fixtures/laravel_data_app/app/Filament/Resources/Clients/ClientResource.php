<?php

namespace App\Filament\Resources\Clients;

use App\Models\Client;
use Filament\Resources\Resource;

class ClientResource extends Resource
{
    protected static ?string $model = Client::class;

    public static function getRelations(): array
    {
        return [
            RelationManagers\BranchesRelationManager::class,
        ];
    }

    public static function getPages(): array
    {
        return [
            'index' => Pages\ListBranches::route('/'),
            'edit' => Pages\EditClient::route('/{record}/edit'),
        ];
    }
}

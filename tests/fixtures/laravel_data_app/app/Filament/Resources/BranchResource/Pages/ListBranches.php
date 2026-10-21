<?php

namespace App\Filament\Resources\BranchResource\Pages;

use Filament\Resources\Pages\ListRecords;

class ListBranches extends ListRecords
{
    // The page names its resource back: a generic references_constant on the same node pair.
    protected static string $resource = \App\Filament\Resources\BranchResource::class;
}

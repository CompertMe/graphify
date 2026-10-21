<?php

namespace App\Console\Commands;

use Illuminate\Console\Attributes\AsCommand;
use Illuminate\Console\Command;

#[AsCommand(name: 'app:attr', description: 'Declared through the attribute')]
class AttributeCommand extends Command
{
    public function handle(): void
    {
        $this->call(BackupRun::class, ['target' => '/tmp']);
    }
}

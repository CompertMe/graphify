<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;

class BackupRun extends Command
{
    protected $signature = 'backup:run
        {target : Where to write}
        {--keep=7}';

    public function handle(): void
    {
    }
}

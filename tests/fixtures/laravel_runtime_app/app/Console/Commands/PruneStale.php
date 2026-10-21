<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;

class PruneStale extends Command
{
    protected $name = 'stale:prune';

    public function handle(): void
    {
        $this->callSilent('reports:send', ['--force' => true]);
    }
}

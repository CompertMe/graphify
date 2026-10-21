<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;

class SendReports extends Command
{
    protected $signature = 'reports:send {user?} {--force : Skip the guard}';

    protected $description = 'Send the nightly reports';

    public function handle(): int
    {
        $this->call('stale:prune');

        return self::SUCCESS;
    }
}

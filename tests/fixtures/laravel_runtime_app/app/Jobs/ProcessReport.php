<?php

namespace App\Jobs;

use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;

class ProcessReport implements ShouldQueue
{
    use Dispatchable, Queueable;

    public function __construct(public int $id = 0)
    {
    }

    public function handle(): void
    {
    }
}

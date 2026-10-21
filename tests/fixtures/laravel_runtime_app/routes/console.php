<?php

use App\Console\Commands\BackupRun;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Schedule;

Artisan::command('inspire', function () {
    $this->comment('inspire');
})->purpose('Display an inspiring quote');

Schedule::command('inspire')->hourly();
Schedule::command(BackupRun::class, ['/var'])->twiceDaily(1, 13);
Schedule::command('reports:send')->everyTenMinutes();

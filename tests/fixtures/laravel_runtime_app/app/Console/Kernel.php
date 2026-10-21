<?php

namespace App\Console;

use App\Console\Commands\PruneStale;
use App\Jobs\ProcessReport;
use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Foundation\Console\Kernel as ConsoleKernel;

class Kernel extends ConsoleKernel
{
    protected function schedule(Schedule $schedule): void
    {
        $schedule->command('reports:send --force')->daily();
        $schedule->command(PruneStale::class)->cron('*/5 * * * *');
        $schedule->command('backup:run', ['/tmp'])->dailyAt('13:00')->withoutOverlapping();
        $schedule->command('inspire')->hourly();
        $schedule->command('app:attr')
            ->everyFiveMinutes();
        $schedule->command('dup:cmd')->weekly();
        $schedule->command('vendor:publish')->monthly();
        $schedule->command('not:declared' . config('x'))->daily();
        $schedule->job(new ProcessReport())->everyMinute();
        $schedule->job(ProcessReport::class)->hourlyAt(17);
    }

    protected function commands(): void
    {
        $this->load(__DIR__.'/Commands');
    }
}

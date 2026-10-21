<?php

namespace App\Http\Controllers;

use App\Events\InvoicePaid;
use App\Events\OrderShipped;
use App\Events\UserRegistered;
use App\Jobs\ChildJob;
use App\Jobs\LastJob;
use App\Jobs\PlainJob;
use App\Jobs\ProcessReport;
use App\Jobs\SyncJob;
use App\Jobs\VendorTraitJob;
use App\Mail\WelcomeMail;
use App\Notifications\InvoiceNotification;
use App\Services\Helper;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Bus;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Facades\Notification;
use Vendor\Package\RemoteJob;

class ReportController
{
    public function store($user, $users, $cond): void
    {
        ProcessReport::dispatch(1);
        ProcessReport::dispatchIf($cond, 2);
        ChildJob::dispatchSync();
        dispatch(new SyncJob());
        Bus::dispatch(new LastJob);
        Helper::dispatch('not a job');
        RemoteJob::dispatch();
        $name = 'dynamic';
        $name::dispatch();
        VendorTraitJob::dispatch();
        PlainJob::dispatch();
    }

    public function pipeline(): void
    {
        Bus::chain([
            new ProcessReport(1),
            ChildJob::class,
            new Helper(),
        ])->dispatch();
        Bus::batch([[new ProcessReport(), new ChildJob()], new LastJob()])->dispatch();
        ProcessReport::dispatch()->chain([new ChildJob(), LastJob::class]);
    }

    public function events($user, $users): void
    {
        event(new UserRegistered());
        OrderShipped::dispatch(5);
        Event::dispatch(new InvoicePaid());
        $user->notify(new InvoiceNotification());
        Notification::send($users, new InvoiceNotification());
        Notification::route('mail', 'ops@example.com')->notify(new InvoiceNotification());
        Mail::to($user)->send(new WelcomeMail());
        Mail::to($user)->cc('x@example.com')->queue(new WelcomeMail());
    }

    public function artisan(): void
    {
        Artisan::call('backup:run', ['target' => '/tmp']);
        Artisan::queue('reports:send');
        Artisan::call('dup:cmd');
        Artisan::call('vendor:publish');
        Artisan::call('app:attr' . 'x');
        Artisan::call(\App\Console\Commands\PruneStale::class);
    }
}

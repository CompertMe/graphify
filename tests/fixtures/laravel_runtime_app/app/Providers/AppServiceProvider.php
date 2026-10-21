<?php

namespace App\Providers;

use App\Events\ClosureEvent;
use App\Events\InvoicePaid;
use App\Events\OrderShipped;
use App\Listeners\ShipListener;
use App\Models\Comment;
use App\Models\User;
use App\Observers\AuditObserver;
use App\Observers\CommentObserver;
use App\Services\Contracts\Clock;
use App\Services\Contracts\Mailer;
use App\Services\Contracts\Payment;
use App\Services\Contracts\Ambiguous;
use App\Services\Contracts\Tracker;
use App\Services\Impl\FlakyThing;
use App\Services\Impl\Gateway;
use App\Services\Impl\ReportService;
use App\Services\Impl\SmtpMailer;
use App\Services\Impl\SystemClock;
use App\Services\Impl\TrackerImpl;
use App\Services\SelfBound;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\ServiceProvider;
use Laravel\Fortify\Contracts\LoginResponse as LoginResponseContract;
use App\Http\Responses\LoginResponse;

class AppServiceProvider extends ServiceProvider
{
    public array $bindings = [
        Tracker::class => TrackerImpl::class,
    ];

    public $singletons = [
        Mailer::class => SmtpMailer::class,
        SelfBound::class,
    ];

    public function register(): void
    {
        $this->app->bind(Payment::class, Gateway::class);
        $this->app->singleton(Clock::class, fn () => new SystemClock());
        $this->app->scoped(Ambiguous::class, function ($app) {
            return $app->make(FlakyThing::class);
        });
        $this->app->bind(Ambiguous::class, SystemClock::class);
        $this->app->bind(Tracker::class, function () {
            return random_int(0, 1) ? new FlakyThing() : new SystemClock();
        });
        $this->app->singleton(SelfBound::class);
        $this->app->singleton(LoginResponseContract::class, LoginResponse::class);
        $this->app->bind('reports', ReportService::class);
        $this->app->bind($this->abstract(), Gateway::class);
    }

    public function boot(): void
    {
        Comment::observe(CommentObserver::class);
        User::observe([AuditObserver::class]);
        Event::listen(OrderShipped::class, [ShipListener::class, 'onShipped']);
        Event::listen(function (ClosureEvent $event) {
        });
        Event::listen(InvoicePaid::class, function ($event) {
        });
    }

    private function abstract(): string
    {
        return Payment::class;
    }
}

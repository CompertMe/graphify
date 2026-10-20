<?php

namespace App\Providers;

use App\Http\Middleware\EnsureAdmin;
use App\Http\Middleware\Locale;
use Filament\Http\Middleware\Authenticate;
use Filament\Panel;
use Filament\PanelProvider;

class AdminPanelProvider extends PanelProvider
{
    public function panel(Panel $panel): Panel
    {
        return $panel
            ->id('admin')
            ->path('cp')
            ->middleware([
                Locale::class,
                \Illuminate\Session\Middleware\StartSession::class,
            ])
            ->authMiddleware([
                Authenticate::class,
                EnsureAdmin::class,
            ]);
    }
}

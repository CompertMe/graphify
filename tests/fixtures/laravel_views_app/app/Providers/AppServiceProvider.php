<?php

namespace App\Providers;

use App\Livewire\Counter;
use App\View\Components\Alert;
use Illuminate\Support\Facades\Blade;
use Illuminate\Support\Facades\View;
use Illuminate\Support\ServiceProvider;
use Livewire\Livewire;

class AppServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        // namespaces: literal paths only
        $this->loadViewsFrom(__DIR__.'/../../modules/Blog/Resources/views', 'blog');
        View::addNamespace('shop', base_path('modules/Shop/Resources/views'));
        // same name registered in two directories (ambiguous), plus one that is not a literal
        $this->loadViewsFrom(__DIR__.'/../../modules/Blog/Resources/views', 'twin');
        $this->loadViewsFrom(__DIR__.'/../../modules/Shop/Resources/views', 'twin');
        $this->loadViewsFrom($this->dynamicPath(), 'dyn');
        $this->loadViewsFrom(__DIR__.'/../../modules/Blog/Resources/views', 'dyn');

        // components
        Blade::anonymousComponentPath(resource_path('views/ui'), 'ui');
        $themed = resource_path('views/themed');          // a variable assigned once is followed
        Blade::anonymousComponentPath($themed);
        $twice = resource_path('views/twice-a');          // assigned twice: not a fact
        $twice = resource_path('views/twice-b');
        Blade::anonymousComponentPath($twice, 'twice');
        Blade::component('pkg-alert', Alert::class);
        Livewire::component('legacy-counter', Counter::class);
    }
}

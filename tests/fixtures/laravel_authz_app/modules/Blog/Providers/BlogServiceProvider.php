<?php

namespace Modules\Blog\Providers;

use Illuminate\Support\ServiceProvider;

class BlogServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->mergeConfigFrom(__DIR__.'/../config/config.php', 'modules.blog');
        $this->loadTranslationsFrom(__DIR__.'/../resources/lang', 'blog');
    }
}

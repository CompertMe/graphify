<?php

return [
    'name' => env('APP_NAME', 'Laravel'),
    'debug' => (bool) env('APP_DEBUG', false),
    'locale' => 'en',
    'providers' => [
        App\Providers\AuthServiceProvider::class,
    ],
    'cipher' => 'AES-256-CBC',
];

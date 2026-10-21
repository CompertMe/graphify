<?php

return [
    'stripe' => [
        'key' => env('STRIPE_KEY'),
        'secret' => env('STRIPE_SECRET'),
        'options' => [
            'retry' => [
                'max' => 3,
                'deep' => [
                    'deeper' => 1,
                ],
            ],
        ],
    ],
    'mailgun' => [
        'domain' => env('MAILGUN_DOMAIN'),
        'endpoint' => env('MAILGUN_ENDPOINT', 'api.mailgun.net'),
    ],
    'plans' => ['free', 'pro'],
];

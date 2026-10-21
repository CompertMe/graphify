<?php

namespace App\Services;

use Illuminate\Support\Facades\Config;
use Illuminate\Support\Facades\Lang;

class Reporter
{
    public function run()
    {
        $name = config('app.name');
        $key = config('services.stripe.key', 'x');
        $deepParent = config('services.stripe.key.nested');
        $branch = config('services.stripe');          // names a branch: the config file
        config(['app.locale' => 'ar']);
        $a = Config::get('services.mailgun.domain');
        $b = config()->get('modules.blog.per_page');
        $c = config('no.such.key');
        $d = config($this->dynamic());
        $e = config("services.{$name}.key");
        $f = Config::boolean('app.debug');
        $deep = config('services.stripe.options.retry.max');
        $cut = config('services.stripe.options.retry.deep.deeper');
        $plans = config('services.plans');
    }

    public function texts()
    {
        $a = __('messages.welcome');
        $b = trans('messages.errors.not_found');
        $c = trans_choice('messages.goodbye', 2);
        $d = Lang::get('blog::posts.title');
        $e = __('Save changes');
        $f = __('messages.missing_one');
        $g = __('Free text with no json entry');
        $h = __('messages.errors');           // a branch, not missing
        $i = __($this->key());
        $j = __('messages.days');
        $k = __('Hello, :name!', ['name' => 'x']);
    }
}

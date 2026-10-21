<?php

namespace App\Rules;

use Closure;
use Illuminate\Contracts\Validation\ValidationRule;

class ValidCode implements ValidationRule
{
    public function __construct(private bool $strict = false)
    {
    }

    public function validate(string $attribute, mixed $value, Closure $fail): void
    {
    }
}

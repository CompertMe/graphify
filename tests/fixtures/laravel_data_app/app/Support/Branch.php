<?php

namespace App\Support;

// Not an Eloquent model: a plain class that shares the short name `Branch`.
class Branch
{
    public static function where(string $key, $value): array
    {
        return [$key => $value];
    }
}

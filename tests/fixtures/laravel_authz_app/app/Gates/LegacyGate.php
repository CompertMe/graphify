<?php

namespace App\Gates;

class LegacyGate
{
    public function check($user): bool
    {
        return true;
    }

    public function manage($user): bool
    {
        return true;
    }

    public function __invoke($user): bool
    {
        return true;
    }
}

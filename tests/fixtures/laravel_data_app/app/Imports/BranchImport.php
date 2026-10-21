<?php

namespace App\Imports;

use Maatwebsite\Excel\Concerns\ToCollection;
use Maatwebsite\Excel\Concerns\WithValidation;

class BranchImport implements ToCollection, WithValidation
{
    public function collection($rows): void
    {
    }

    public function rules(): array
    {
        return [
            '*.client_code' => ['required', 'exists:clients,code'],
        ];
    }
}

<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

class UpdateBranchRequest extends FormRequest
{
    public function rules(): array
    {
        // Rules assembled in variables: no single `return [...]` holds them.
        $categoryRules = ['required', Rule::exists('categories', 'id')];
        $rules = ['code' => 'required|string'];
        $rules['category_id'] = $categoryRules;
        $rules['tag'] = 'sometimes|exists:tags,id';
        $rules['via_unknown'] = 'exists:'.config('x').',id';

        return $rules;
    }
}

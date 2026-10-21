<?php

namespace App\Http\Requests;

use App\Enums\BranchType;
use App\Enums\ClientStatus;
use App\Models\Category;
use App\Rules\ValidCode;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Rules\Enum;

class StoreBranchRequest extends FormRequest
{
    public function rules(): array
    {
        $table = 'ghosts';

        return [
            'client_id' => ['required', Rule::exists('clients', 'id')],
            'name' => 'required|string|unique:branches,name,'.$this->route('branch'),
            'type' => ['required', Rule::enum(BranchType::class)],
            'status' => ['required', new Enum(ClientStatus::class)],
            'code' => ['required', new ValidCode(strict: true)],
            'manager_id' => 'nullable|exists:users,id',
            'category_id' => [Rule::exists(Category::class, 'id')],
            'ghost_id' => 'exists:ghosts,id',
            'dynamic_id' => 'exists:'.$table.',id',
            'emails.*' => 'unique:App\Models\Person,email',
            'tag_ids.*' => [Rule::unique('tags')->ignore($this->tag)->where('label', 'x')],
            'size' => ['in:small,large', Rule::in(['small', 'large'])],
        ];
    }
}

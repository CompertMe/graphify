<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Validation\Rule;

class ProfileController
{
    public function update(Request $request, bool $strict)
    {
        // Each branch of the ternary is a rule set the field can get.
        $request->validate([
            'email' => $strict
                ? ['required', Rule::unique('users', 'email')]
                : ['nullable'],
        ]);
    }

    public function notify(Request $request, bool $editing)
    {
        $rules = ['title' => ['required']];

        if (! $editing) {
            $rules['client_id'] = ['integer', 'exists:clients,id'];
        }

        $request->validate($rules);
    }

    public function elsewhere(Request $request)
    {
        // `$rules` is not built in this function: nothing is guessed.
        $request->validate($this->rules);
        $request->validate($request->rules());
    }

    public function unrelated(Request $request)
    {
        $rules = ['tag' => ['exists:tags,id']];
        $other = ['x' => ['exists:branches,id']];
        $request->validate($other);
    }
}

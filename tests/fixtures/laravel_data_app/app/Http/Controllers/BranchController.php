<?php

namespace App\Http\Controllers;

use App\Models\Branch;
use App\Models\Client;
use App\Models\Ghost;
use App\Models\User;
use App\Enums\ClientStatus;
use App\Rules\ValidCode;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Validator;

class BranchController
{
    public function index()
    {
        $active = Branch::active()->count();

        return Branch::query()->with('client')->latest()->get();
    }

    public function show(int $id)
    {
        return Branch::findOrFail($id);
    }

    public function store(Request $request)
    {
        $data = $request->validate([
            'client_id' => 'required|exists:clients,id',
            'owner' => ['required', new ValidCode],
        ]);

        $validator = Validator::make($request->all(), [
            'manager_id' => ['exists:users,id'],
        ]);

        Client::firstOrCreate(['email' => $data['email']]);

        return Branch::create($data);
    }

    public function byCode(string $code)
    {
        // Declared on the model: the call resolver owns this edge, not the Eloquent static pass.
        return Branch::fromCode($code);
    }

    public function cases()
    {
        $now = ClientStatus::from('active');   // an enum, not a model
        $x = Ghost::all();
        $this->helper();

        return User::where('id', 1)->first();
    }

    private function helper(): void
    {
        $fn = function () {
            return Client::all();
        };
        $fn();
    }
}

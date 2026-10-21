<?php

use App\Http\Controllers\ContactController;
use Illuminate\Support\Facades\Route;

Route::middleware(['auth', 'can:create-branches'])->group(function () {
    Route::get('/branches', [ContactController::class, 'branches'])->name('branches');
});

Route::get('/contacts', [ContactController::class, 'index'])->middleware('can:viewAny,App\Models\Contact');
Route::get('/contacts/edit', [ContactController::class, 'edit'])->can('edit-contacts');
Route::get('/unknown', [ContactController::class, 'edit'])->middleware('can:not-defined-anywhere');

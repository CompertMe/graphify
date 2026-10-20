<?php

use App\Http\Controllers\PageController;
use Illuminate\Support\Facades\Route;

// Nothing in this project loads this file: its inheritance chain is unknown.
Route::get('/orphan', [PageController::class, 'home'])->name('orphan');

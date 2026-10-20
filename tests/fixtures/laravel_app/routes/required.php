<?php

use App\Http\Controllers\PageController;
use Illuminate\Support\Facades\Route;

Route::get('/required', [PageController::class, 'audit'])->name('required');

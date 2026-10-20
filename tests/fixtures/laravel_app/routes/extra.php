<?php

use App\Http\Controllers\PageController;
use Illuminate\Support\Facades\Route;

Route::get('/pages', [PageController::class, 'index'])->name('pages');

<?php

use App\Http\Controllers\PageController;
use Illuminate\Support\Facades\Route;

Route::get('/page', [PageController::class, 'index'])->name('page');

Route::prefix('deep')->group(function () {
    Route::get('/shared', [PageController::class, 'shared'])->name('shared');
});

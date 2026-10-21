<?php

use App\Http\Controllers\ReportController;
use Illuminate\Support\Facades\Route;

Route::view('/welcome', 'welcome')->name('welcome');
Route::view('/ghost', 'ghost.page');
Route::get('/report', [ReportController::class, 'show'])->name('report');
Route::get('/closure', function () {
    return view('reports.partials.chart');
});

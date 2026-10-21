<?php

use App\Http\Controllers\BranchController;
use Illuminate\Support\Facades\Route;
use Inertia\Inertia;

Route::get('/', fn () => view('welcome'))->name('home');

Route::prefix('branches')->name('tenant.branches.')->group(function () {
    Route::get('/', [BranchController::class, 'index'])->name('index');
    Route::get('/create', [BranchController::class, 'create'])->name('create');
    Route::get('/{branch}', [BranchController::class, 'show'])->name('show');
    Route::post('/', [BranchController::class, 'store'])->name('store');
    Route::get('/{branch}/edit', [BranchController::class, 'edit'])->name('edit');
    Route::get('/dynamic', [BranchController::class, 'dynamic'])->name('dynamic');
});

Route::inertia('/about', 'About', ['title' => 'About us'])->name('about');
Route::inertia('/missing', 'Does/Not/Exist')->name('missing');
Route::get('/closure-page', fn () => Inertia::render('Closure/Page'))->name('closure.page');

if (config('app.legacy')) {
    Route::get('/legacy', fn () => 'a')->name('dup');
} else {
    Route::get('/legacy', fn () => 'b')->name('dup');
}

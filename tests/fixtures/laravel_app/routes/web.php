<?php

use App\Http\Controllers\Admin\DashboardController;
use App\Http\Controllers\Admin\ReportController;
use App\Http\Controllers\BranchController;
use App\Http\Controllers\InvokableController;
use App\Http\Controllers\PageController;
use App\Http\Controllers\PostController;
use App\Http\Controllers\ProfileController;
use App\Http\Controllers\VendorChildController;
use App\Http\Middleware\ExplicitMiddleware;
use App\Livewire\Dashboard;
use Illuminate\Support\Facades\Route;

Route::get('/', [PageController::class, 'home'])->name('home');
Route::post('/branches', [BranchController::class, 'store'])->name('branches.store');
Route::get('/branches/{branch}', 'App\Http\Controllers\BranchController@show')->name('branches.show');
Route::get('/health', InvokableController::class);
Route::get('/closure', function () {
    return 'ok';
})->name('closure');
Route::get('/arrow', fn () => 'arrow');
Route::get('/vendor-only', [VendorChildController::class, 'callAction']);
Route::get('/ghost', [PageController::class, 'missingMethod']);
Route::get('/nowhere', [\App\Http\Controllers\NoSuchController::class, 'index']);
Route::view('/welcome', 'welcome', ['x' => 1])->name('welcome');
Route::redirect('/old', '/new', 301);
Route::match(['get', 'post'], '/multi', [PageController::class, 'multi']);
Route::get('/dashboard', Dashboard::class)->middleware(ExplicitMiddleware::class);
Route::livewire('/live', Dashboard::class)->name('live');

Route::controller(PageController::class)->group(function () {
    Route::get('/pages', 'index');
    Route::get('/pages/{page}', 'show')->name('pages.show');
});

Route::resource('posts', PostController::class)->except(['destroy', 'edit']);
Route::resource('branches', BranchController::class)->only(['index', 'update']);
Route::singleton('profile', ProfileController::class);

Route::prefix('admin')->name('admin.')->middleware(['auth', 'admin'])->group(function () {
    Route::get('/dashboard', [DashboardController::class, 'index'])->name('dashboard');
    Route::prefix('reports')->name('reports.')->middleware('can:view-reports')->group(function () {
        Route::get('/', [ReportController::class, 'index'])->name('index');
        Route::get('/audit', [ReportController::class, 'audit'])->name('audit')->withoutMiddleware('admin');
        Route::get('/shared', [ReportController::class, 'shared']);
    });
});

Route::group(['prefix' => 'legacy', 'as' => 'legacy.', 'middleware' => ['visits', 'legacy']], function () {
    Route::get('/x', [PageController::class, 'home'])->name('x');
});

Route::middleware('tracked')->group(function () {
    Route::get('/tracked', [PageController::class, 'index'])->middleware('kernel.mw');
});

Route::middleware(ExplicitMiddleware::class)->prefix('inc')->name('inc.')->group(__DIR__.'/included.php');

Route::get('/dyn/'.$suffix, [PageController::class, 'home']);

require __DIR__.'/required.php';

<?php

use Illuminate\Support\Facades\Route;
use Modules\Blog\Http\Controllers\ArticleController;

Route::middleware(['web', 'admin'])->prefix('blog')->name('blog.')->group(function () {
    Route::get('/articles', [ArticleController::class, 'index'])->name('articles.index');
});

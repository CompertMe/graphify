<?php

use App\Http\Controllers\Api\TagController;
use App\Http\Controllers\Api\UserController;
use Illuminate\Support\Facades\Route;

Route::apiResource('users', UserController::class)->only(['index', 'show']);
Route::apiResources(['tags' => TagController::class]);
Route::get('/ping', fn () => 'pong')->name('ping');

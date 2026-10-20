<?php

use App\Http\Controllers\PageController;
use Illuminate\Support\Facades\Route;

Route::get('/ping', [PageController::class, 'home'])->name('rsp.ping');

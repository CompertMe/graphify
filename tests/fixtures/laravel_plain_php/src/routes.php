<?php

use Plain\Handler;

// Looks like Laravel routing, but nothing marks this project as Laravel.
Route::get('/plain', [Handler::class, 'index']);

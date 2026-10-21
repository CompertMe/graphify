<?php

namespace App\Observers;

use App\Models\Post;

class PostObserver
{
    public function created(Post $post): void
    {
    }
}

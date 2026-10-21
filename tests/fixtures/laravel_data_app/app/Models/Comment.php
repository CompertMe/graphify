<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Comment extends Model
{
    public function commentable()
    {
        return $this->morphTo();
    }

    public function tags()
    {
        return $this->morphToMany(Tag::class, 'taggable');
    }

    public function pinnedTag()
    {
        return $this->morphOne(Tag::class, 'taggable');
    }
}

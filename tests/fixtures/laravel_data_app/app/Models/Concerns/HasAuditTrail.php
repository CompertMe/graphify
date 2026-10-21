<?php

namespace App\Models\Concerns;

use App\Models\Comment;

trait HasAuditTrail
{
    public function auditNotes()
    {
        return $this->morphMany(Comment::class, 'commentable');
    }
}

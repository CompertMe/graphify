<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

// A migration in a sub-folder (tenant migrations): replayed like any other.
return new class extends Migration
{
    public function up(): void
    {
        Schema::connection('tenant')->create('audits', function (Blueprint $table) {
            $table->id();
            $table->foreignId('user_id')->constrained();
            $table->string('event');
        });
    }
};

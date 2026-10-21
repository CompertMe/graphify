<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('clients', function (Blueprint $table) {
            $table->id();
            $table->string('name', 120);
            $table->string('email')->unique();
            $table->string('status')->default('active');
            $table->json('meta')->nullable();
            $table->timestamp('joined_at')->nullable();
            $table->timestamps();
            $table->softDeletes();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('clients');
        Schema::dropIfExists('never_created');
    }
};

<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('tags', function (Blueprint $table) {
            $table->id();
            $table->string('label');
        });

        Schema::create('branch_tag', function (Blueprint $table) {
            $table->foreignId('branch_id')->constrained()->cascadeOnDelete();
            $table->foreignId('tag_id')->constrained();
            $table->primary(['branch_id', 'tag_id']);
        });

        Schema::create('people', function (Blueprint $table) {
            $table->id();
            $table->string('email')->unique();
        });

        Schema::create('categories', function (Blueprint $table) {
            $table->id();
            $table->string('title');
        });

        Schema::create('addresses', function (Blueprint $table) {
            $table->id();
            $table->foreignIdFor(\App\Models\Person::class)->constrained();
        });

        Schema::create('comments', function (Blueprint $table) {
            $table->id();
            $table->morphs('commentable');
            $table->text('body');
        });

        Schema::create('shared_records', function (Blueprint $table) {
            $table->id();
        });

        foreach (['dyn_a', 'dyn_b'] as $name) {
            Schema::create($name, function (Blueprint $table) {
                $table->id();
            });
        }
    }
};

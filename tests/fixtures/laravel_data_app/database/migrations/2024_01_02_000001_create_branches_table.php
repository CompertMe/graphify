<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class CreateBranchesTable extends Migration
{
    public function up()
    {
        Schema::create('branches', function (Blueprint $table) {
            $table->id();
            $table->foreignId('client_id')->constrained()->cascadeOnDelete();
            $table->foreignId('manager_id')->nullable()->constrained('users')->nullOnDelete();
            $table->string('name');
            $table->string('type');
            $table->enum('size', ['small', 'large'])->default('small');
            $table->timestamp('opened_at')->nullable();
            $table->nullableMorphs('owner');
            $table->timestamps();
        });
    }

    public function down()
    {
        Schema::dropIfExists('branches');
    }
}

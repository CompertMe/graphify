<?php

use App\Models\Category;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('branches', function (Blueprint $table) {
            $table->string('code')->nullable()->unique();
            $table->foreignIdFor(Category::class)->nullable()->constrained();
            $table->dropColumn('size');
            $table->renameColumn('opened_at', 'opens_on');
        });
    }

    public function down(): void
    {
        Schema::table('branches', function (Blueprint $table) {
            $table->dropColumn('code');
            $table->string('removed_in_down');
        });
    }
};

<?php

if (! function_exists('theme_view')) {
    function theme_view(string $view, ?string $area = null): string
    {
        $area ??= current_area();

        return "theme-{$area}::{$view}";
    }
}

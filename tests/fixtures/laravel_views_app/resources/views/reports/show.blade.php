@extends('layouts.app')

@push('scripts')
    <script>const a = 1;</script>
@endpush

@section('content')
    {{-- @include('reports.partials.commented') <x-button>commented</x-button> --}}
    @include('reports.partials.summary', ['a' => 1])
    @includeIf('reports.partials.chart')
    @includeWhen($flag, 'reports.partials.chart')
    @includeUnless($flag, 'reports.partials.summary')
    @includeFirst(['reports.partials.chart', 'reports.partials.summary'])
    @includeFirst(['reports.partials.nope', 'reports.partials.summary'])
    @each('reports.partials.summary', $rows, 'row', 'reports.partials.chart')
    @include('reports.partials.' . $dynamic)
    @include($variable)
    @include('blog::index')
    @include('reports.partials.missing')
    @include(theme_view('auth.login', 'guest'))
    @@include('reports.partials.escaped')
    @component('components.alert')
        legacy
    @endcomponent
    @verbatim
        @include('reports.partials.verbatim')
        <x-button>verbatim</x-button>
    @endverbatim

    <x-button type="primary">Go</x-button>
    <x-button>Again</x-button>
    <x-forms.field name="a" />
    <x-forms.input name="b" />
    <x-alert>class component</x-alert>
    <x-pkg-alert>aliased class component</x-pkg-alert>
    <x-panel />
    <x-ui::tile />
    <x-badge />
    <x-twice::thing />
    <x-blog::card />
    <x-missing-thing />
    <x-slot:title>title</x-slot:title>
    <x-dynamic-component :component="$name" />

    <livewire:counter />
    <livewire:legacy-counter />
    <livewire:admin.user-table :rows="$rows" />
    <livewire:child />
    <livewire:twin />
    <livewire:inline-note />
    <livewire:nothing-here />
    @livewire('counter', ['start' => 1])
    @livewire(\App\Livewire\Admin\UserTable::class)
    @livewire($dynamic)
@endsection

@section('sidebar', 'side')

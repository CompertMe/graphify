{{-- @can('create-branches') in a comment is ignored --}}
@can('viewAny', App\Models\Contact::class)
    <h1>{{ __('messages.welcome') }}</h1>
@endcan
@cannot('close-books')
    <p>@lang('messages.goodbye')</p>
@endcannot
@canany(['manage-legacy', 'invokable-gate', 'never-defined'])
    <p>{{ config('app.name') }} {{ trans('Save changes') }}</p>
@endcanany
@can($dynamic)
    <p>{{ __('messages.gone') }}</p>
@endcan
{{-- {{ __('messages.commented') }} --}}

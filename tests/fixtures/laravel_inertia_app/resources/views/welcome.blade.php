<a href="{{ route('tenant.branches.index') }}">Branches</a>
{{-- <a href="{{ route('tenant.branches.create') }}">commented out</a> --}}
<form action="{{ route('tenant.branches.store') }}"></form>
<a href="{{ route('not.a.route') }}">x</a>
<a href="{{ $request->route('branch') }}">param, not a route name</a>

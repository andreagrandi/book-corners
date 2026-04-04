from functools import wraps

from django.shortcuts import redirect


def staff_required(view_func):
    """Restrict access to staff users, redirecting others to the login page."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return wrapper

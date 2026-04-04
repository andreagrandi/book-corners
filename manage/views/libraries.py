from django.shortcuts import render

from manage.decorators import staff_required


@staff_required
def library_list(request):
    """List libraries with filtering, search, and moderation actions."""
    return render(request, "manage/libraries/list.html")

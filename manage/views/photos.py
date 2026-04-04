from django.shortcuts import render

from manage.decorators import staff_required


@staff_required
def photo_list(request):
    """List library photos with filtering and moderation actions."""
    return render(request, "manage/photos/list.html")

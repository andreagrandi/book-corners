from django.shortcuts import render

from manage.decorators import staff_required


@staff_required
def user_list(request):
    """List users with search and filtering."""
    return render(request, "manage/users/list.html")

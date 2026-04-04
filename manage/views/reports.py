from django.shortcuts import render

from manage.decorators import staff_required


@staff_required
def report_list(request):
    """List reports with filtering and moderation actions."""
    return render(request, "manage/reports/list.html")

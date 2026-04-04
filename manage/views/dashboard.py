from django.shortcuts import render

from manage.decorators import staff_required


@staff_required
def dashboard(request):
    """Render the custom admin dashboard with moderation queue summaries."""
    return render(request, "manage/dashboard.html")

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from manage.decorators import staff_required
from users.models import User

USERS_PER_PAGE = 25


@staff_required
def user_list(request: HttpRequest) -> HttpResponse:
    """List users with search and filtering."""
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    qs = User.objects.annotate(library_count=Count("libraries")).all()

    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
    if role == "staff":
        qs = qs.filter(is_staff=True)
    elif role == "active":
        qs = qs.filter(is_active=True, is_staff=False)
    elif role == "inactive":
        qs = qs.filter(is_active=False)

    qs = qs.order_by("-date_joined")
    paginator = Paginator(qs, USERS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "total_count": paginator.count,
        "current_q": q,
        "current_role": role,
    }

    if request.headers.get("HX-Request"):
        return render(request, "manage/users/_table.html", context)
    return render(request, "manage/users/list.html", context)


@staff_required
def user_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Display user detail with their submissions."""
    user = get_object_or_404(User, pk=pk)
    libraries = user.libraries.all()[:20]
    reports = user.reports.select_related("library").all()[:10]
    context = {
        "profile_user": user,
        "libraries": libraries,
        "reports": reports,
    }
    return render(request, "manage/users/detail.html", context)

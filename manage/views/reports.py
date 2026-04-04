from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from libraries.models import Report
from manage.decorators import staff_required

REPORTS_PER_PAGE = 25


def _report_htmx_response(request: HttpRequest, report: Report) -> HttpResponse:
    """Return the appropriate HTMX partial for a report action."""
    target = request.headers.get("HX-Target", "")
    if target.startswith("library-report-"):
        template = "manage/libraries/_report_card.html"
    else:
        template = "manage/reports/_row.html"
    return render(request, template, {"report": report})


@staff_required
def report_list(request: HttpRequest) -> HttpResponse:
    """List reports with status and reason filtering."""
    status = request.GET.get("status", "")
    reason = request.GET.get("reason", "")
    qs = Report.objects.select_related("library", "created_by").all()

    if status:
        qs = qs.filter(status=status)
    if reason:
        qs = qs.filter(reason=reason)

    paginator = Paginator(qs, REPORTS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "current_status": status,
        "current_reason": reason,
        "total_count": paginator.count,
        "status_choices": Report.Status.choices,
        "reason_choices": Report.Reason.choices,
    }

    if request.headers.get("HX-Request"):
        return render(request, "manage/reports/_table.html", context)
    return render(request, "manage/reports/list.html", context)


@staff_required
@require_POST
def report_resolve(request: HttpRequest, pk: int) -> HttpResponse:
    """Resolve a single report."""
    report = get_object_or_404(
        Report.objects.select_related("library", "created_by"), pk=pk
    )
    report.status = Report.Status.RESOLVED
    report.save(update_fields=["status"])

    if request.headers.get("HX-Request"):
        return _report_htmx_response(request, report)
    return redirect("manage:report_list")


@staff_required
@require_POST
def report_dismiss(request: HttpRequest, pk: int) -> HttpResponse:
    """Dismiss a single report."""
    report = get_object_or_404(
        Report.objects.select_related("library", "created_by"), pk=pk
    )
    report.status = Report.Status.DISMISSED
    report.save(update_fields=["status"])

    if request.headers.get("HX-Request"):
        return _report_htmx_response(request, report)
    return redirect("manage:report_list")


@staff_required
@require_POST
def report_bulk_action(request: HttpRequest) -> HttpResponse:
    """Handle bulk resolve/dismiss actions on selected reports."""
    action = request.POST.get("action")
    ids = request.POST.getlist("selected")

    if not ids or action not in ("resolve", "dismiss"):
        return redirect("manage:report_list")

    qs = Report.objects.filter(pk__in=ids)

    if action == "resolve":
        qs.update(status=Report.Status.RESOLVED)
    elif action == "dismiss":
        qs.update(status=Report.Status.DISMISSED)

    return redirect("manage:report_list")

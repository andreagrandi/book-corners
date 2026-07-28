import json
import tempfile

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from libraries.geojson_import import parse_geojson
from libraries.management.commands.find_duplicates import (
    DEFAULT_RADIUS_METERS,
    find_duplicate_groups,
)
from libraries.models import LIBRARY_EDITABLE_FIELDS, Library
from libraries.notifications import (
    notify_library_approved,
    notify_library_rejected,
    notify_library_update_approved,
    notify_library_update_rejected,
)
from libraries.views import (
    GEOJSON_CACHE_KEY,
    HOMEPAGE_COUNT_CACHE_KEY,
    invalidate_cluster_cache,
)
from manage.decorators import staff_required
from manage.forms import LibraryEditForm, LibraryFilterForm
from manage.helpers import render_with_toast

LIBRARIES_PER_PAGE = 25


def _invalidate_library_caches() -> None:
    """Clear caches affected by library data changes.
    Keeps maps, search, and homepage counts in sync."""
    cache.delete(GEOJSON_CACHE_KEY)
    cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
    invalidate_cluster_cache()


def _notify_library_moderation_change(
    *,
    library: Library,
    old_status: str,
    new_status: str,
    rejection_reason: str,
) -> None:
    """Send submitter notifications for moderation status changes.
    Keeps manage edit, approve, and reject paths behaviorally aligned."""
    if old_status == Library.Status.PENDING and new_status == Library.Status.APPROVED:
        notify_library_approved(library)
    if (
        old_status != Library.Status.REJECTED
        and new_status == Library.Status.REJECTED
        and rejection_reason
    ):
        notify_library_rejected(library)


def _get_filtered_libraries(
    request: HttpRequest,
) -> tuple[QuerySet[Library], LibraryFilterForm]:
    """Return libraries filtered by manage list inputs.
    Provides the bound form for rendering the active filters."""
    form = LibraryFilterForm(request.GET)
    qs = Library.objects.select_related("created_by").all()

    if form.is_valid():
        status = form.cleaned_data.get("status")
        country = form.cleaned_data.get("country")
        source = form.cleaned_data.get("source")
        q = form.cleaned_data.get("q")

        if status == Library.Status.PENDING:
            qs = qs.filter(
                Q(status=Library.Status.PENDING)
                | Q(pending_changes__isnull=False)
            )
        elif status == Library.Status.APPROVED:
            qs = qs.filter(
                status=Library.Status.APPROVED,
                pending_changes__isnull=True,
            )
        elif status:
            qs = qs.filter(status=status)
        if country:
            qs = qs.filter(
                Q(country__iexact=country)
                | Q(pending_changes__country__iexact=country)
            )
        if source:
            qs = qs.filter(source__icontains=source)
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(address__icontains=q)
                | Q(city__icontains=q)
                | Q(pending_changes__name__icontains=q)
                | Q(pending_changes__address__icontains=q)
                | Q(pending_changes__city__icontains=q)
            )

    return qs.order_by("-updated_at", "-created_at"), form


def _display_pending_value(
    *,
    library: Library,
    field_name: str,
    value: object,
) -> object:
    """Format a pending-change value for moderator comparison.
    Uses model choice labels and readable empty-value placeholders."""
    if value in ("", None):
        return "—"
    field = library._meta.get_field(field_name)
    if field.choices:
        return dict(field.flatchoices).get(value, value)
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    return value


def _pending_changes_context(*, library: Library) -> list[dict[str, object]]:
    """Build current-versus-proposed rows for the manage detail page.
    Includes only fields whose proposed values differ from the live record."""
    pending_changes: list[dict[str, object]] = []
    changes = library.pending_changes or {}

    for field_name in LIBRARY_EDITABLE_FIELDS:
        if field_name not in changes:
            continue
        field = library._meta.get_field(field_name)
        pending_changes.append(
            {
                "label": field.verbose_name,
                "current": _display_pending_value(
                    library=library,
                    field_name=field_name,
                    value=getattr(library, field_name),
                ),
                "proposed": _display_pending_value(
                    library=library,
                    field_name=field_name,
                    value=changes[field_name],
                ),
            }
        )

    if "latitude" in changes and "longitude" in changes:
        pending_changes.append(
            {
                "label": _("Location"),
                "current": f"{library.location.y:.6f}, {library.location.x:.6f}",
                "proposed": (
                    f"{changes['latitude']:.6f}, {changes['longitude']:.6f}"
                ),
            }
        )

    return pending_changes


@staff_required
def library_list(request: HttpRequest) -> HttpResponse:
    """List libraries with filtering, search, and pagination."""
    qs, form = _get_filtered_libraries(request)

    paginator = Paginator(qs, LIBRARIES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    page.object_list = [
        library.moderation_preview() for library in page.object_list
    ]

    context = {
        "page_obj": page,
        "form": form,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "manage/libraries/_table.html", context)
    return render(request, "manage/libraries/list.html", context)


@staff_required
@require_POST
def library_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve a single library.
    Invalidates library caches and notifies eligible submitters."""
    library = get_object_or_404(Library, pk=pk)
    if library.has_pending_update:
        library.apply_pending_update()
        _invalidate_library_caches()
        notify_library_update_approved(library)
        toast_message = _("Library changes approved.")
        if request.headers.get("HX-Request"):
            return render_with_toast(
                request,
                "manage/libraries/_row.html",
                {"library": library},
                toast_message=toast_message,
            )
        return redirect("manage:library_list")

    old_status = library.status
    library.status = Library.Status.APPROVED
    library.save(update_fields=["status", "updated_at"])
    _invalidate_library_caches()
    _notify_library_moderation_change(
        library=library,
        old_status=old_status,
        new_status=library.status,
        rejection_reason="",
    )

    if request.headers.get("HX-Request"):
        return render_with_toast(
            request, "manage/libraries/_row.html", {"library": library},
            toast_message=_("Library approved."),
        )
    return redirect("manage:library_list")


@staff_required
@require_POST
def library_reject(request: HttpRequest, pk: int) -> HttpResponse:
    """Reject a single library.
    Invalidates library caches and notifies eligible submitters."""
    library = get_object_or_404(Library, pk=pk)
    rejection_reason = request.POST.get("rejection_reason", "")
    if library.has_pending_update:
        library.discard_pending_update()
        if rejection_reason:
            notify_library_update_rejected(
                library,
                rejection_reason=rejection_reason,
            )
        if request.headers.get("HX-Request"):
            return render_with_toast(
                request,
                "manage/libraries/_row.html",
                {"library": library},
                toast_message=_("Library changes rejected."),
            )
        return redirect("manage:library_list")

    old_status = library.status
    library.status = Library.Status.REJECTED
    if rejection_reason:
        library.rejection_reason = rejection_reason
    library.save(update_fields=["status", "rejection_reason", "updated_at"])
    _invalidate_library_caches()
    _notify_library_moderation_change(
        library=library,
        old_status=old_status,
        new_status=library.status,
        rejection_reason=rejection_reason,
    )

    if request.headers.get("HX-Request"):
        return render_with_toast(
            request, "manage/libraries/_row.html", {"library": library},
            toast_message=_("Library rejected."),
        )
    return redirect("manage:library_list")


@staff_required
@require_POST
def library_bulk_action(request: HttpRequest) -> HttpResponse:
    """Handle bulk approve/reject actions on selected libraries."""
    action = request.POST.get("action")
    ids = request.POST.getlist("selected")

    if not ids or action not in ("approve", "reject"):
        return redirect("manage:library_list")

    qs = Library.objects.filter(pk__in=ids)

    libraries = list(qs.select_related("created_by"))
    if action == "approve":
        for library in libraries:
            if library.has_pending_update:
                library.apply_pending_update()
                notify_library_update_approved(library)
            elif library.status == Library.Status.PENDING:
                library.status = Library.Status.APPROVED
                library.save(update_fields=["status", "updated_at"])
                notify_library_approved(library)

    elif action == "reject":
        for library in libraries:
            if library.has_pending_update:
                library.discard_pending_update()
            elif library.status != Library.Status.REJECTED:
                library.status = Library.Status.REJECTED
                library.save(update_fields=["status", "updated_at"])

    _invalidate_library_caches()
    return redirect("manage:library_list")


@staff_required
def library_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Display library detail for review.
    Shows moderation actions, photos, reports, and edit links."""
    library = get_object_or_404(
        Library.objects.select_related("created_by"), pk=pk
    )
    photos = library.user_photos.select_related("created_by").all()[:12]
    reports = library.reports.select_related("created_by").all()[:10]
    context = {
        "library": library,
        "pending_changes": _pending_changes_context(library=library),
        "photos": photos,
        "reports": reports,
    }
    return render(request, "manage/libraries/detail.html", context)


@staff_required
def library_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit core library fields from the manage interface.
    Saves location changes and applies moderation side effects."""
    library = get_object_or_404(
        Library.objects.select_related("created_by"), pk=pk
    )
    old_status = library.status

    if request.method == "POST":
        form = LibraryEditForm(request.POST, instance=library)
        if form.is_valid():
            library = form.save()
            _invalidate_library_caches()
            _notify_library_moderation_change(
                library=library,
                old_status=old_status,
                new_status=library.status,
                rejection_reason=form.cleaned_data["rejection_reason"].strip(),
            )
            messages.success(request, _("Library details saved."))
            return redirect("manage:library_detail", pk=library.pk)
    else:
        form = LibraryEditForm(instance=library)

    context = {
        "library": library,
        "form": form,
    }
    return render(request, "manage/libraries/edit.html", context)


@staff_required
def import_geojson(request: HttpRequest) -> HttpResponse:
    """Handle GeoJSON file upload and queue background import."""
    if request.method != "POST":
        return render(request, "manage/libraries/import.html")

    uploaded_file = request.FILES.get("geojson_file")
    if not uploaded_file:
        messages.error(request, _("Please select a GeoJSON file to upload."))
        return render(request, "manage/libraries/import.html")

    try:
        raw_data = uploaded_file.read().decode("utf-8")
        geojson_data = json.loads(raw_data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        messages.error(request, _("Invalid GeoJSON file: %(error)s") % {"error": exc})
        return render(request, "manage/libraries/import.html")

    source = request.POST.get("source", "").strip()
    status = request.POST.get("status", Library.Status.PENDING)
    if status not in (Library.Status.APPROVED, Library.Status.PENDING):
        status = Library.Status.PENDING

    candidates = parse_geojson(geojson_data)

    imports_dir = settings.MEDIA_ROOT / "geojson_imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=imports_dir, suffix=".json", delete=False, mode="w"
    ) as tmp:
        json.dump(geojson_data, tmp)
        geojson_path = tmp.name

    from libraries.tasks import run_geojson_import

    run_geojson_import.enqueue(
        geojson_path=geojson_path,
        source=source,
        status=status,
        user_id=request.user.pk,
    )

    messages.success(
        request,
        _("Import of %(count)d features has been queued for background processing.")
        % {"count": len(candidates)},
    )
    return redirect("manage:library_list")


@staff_required
def find_duplicates(request: HttpRequest) -> HttpResponse:
    """Scan for duplicate libraries and allow bulk deletion."""
    context = {
        "radius": DEFAULT_RADIUS_METERS,
        "filter_city": "",
        "filter_country": "",
        "use_proximity": True,
        "scanned": False,
        "groups": [],
        "total_duplicates": 0,
        "deleted_count": None,
    }

    if request.method == "POST":
        delete_ids = request.POST.getlist("delete_ids")
        if delete_ids:
            pk_list = [int(pk) for pk in delete_ids]
            deleted_count = Library.objects.filter(pk__in=pk_list).delete()[0]
            _invalidate_library_caches()
            context["deleted_count"] = deleted_count
        return render(request, "manage/libraries/duplicates.html", context)

    radius = int(request.GET.get("radius", DEFAULT_RADIUS_METERS))
    filter_city = request.GET.get("city", "").strip()
    filter_country = request.GET.get("country", "").strip()
    use_proximity = request.GET.get("proximity", "on") == "on"
    scanned = "radius" in request.GET

    context["radius"] = radius
    context["filter_city"] = filter_city
    context["filter_country"] = filter_country
    context["use_proximity"] = use_proximity
    context["scanned"] = scanned

    if scanned:
        groups = find_duplicate_groups(
            radius_meters=radius,
            city=filter_city,
            country=filter_country,
            use_proximity=use_proximity,
        )
        context["groups"] = groups
        context["total_duplicates"] = sum(len(g) - 1 for g in groups)

    return render(request, "manage/libraries/duplicates.html", context)


@staff_required
@require_POST
def ai_enrich(request: HttpRequest, pk: int) -> HttpResponse:
    """Generate AI name and description for a library and show confirmation."""
    library = get_object_or_404(Library, pk=pk)
    if not library.photo:
        messages.error(request, _("This library has no photo for AI analysis."))
        return redirect("manage:library_detail", pk=pk)

    from libraries.social.image_ai import enrich_library_from_image
    from libraries.storage import get_library_photo_path

    image_path = get_library_photo_path(library)
    if not image_path:
        messages.error(request, _("Could not access the library photo."))
        return redirect("manage:library_detail", pk=pk)

    result = enrich_library_from_image(image_path=image_path, library=library)
    if not result:
        messages.error(request, _("AI enrichment failed. Check logs for details."))
        return redirect("manage:library_detail", pk=pk)

    context = {
        "library": library,
        "ai_name": result["name"],
        "ai_description": result["description"],
    }
    return render(request, "manage/libraries/ai_enrich_confirm.html", context)


@staff_required
@require_POST
def ai_enrich_apply(request: HttpRequest, pk: int) -> HttpResponse:
    """Apply AI-generated name and description to a library."""
    library = get_object_or_404(Library, pk=pk)
    ai_name = request.POST.get("ai_name", "").strip()
    ai_description = request.POST.get("ai_description", "").strip()

    update_fields = []
    if ai_name:
        library.name = ai_name[:255]
        update_fields.append("name")
    if ai_description:
        library.description = ai_description[:2000]
        update_fields.append("description")

    if update_fields:
        library.save(update_fields=update_fields)
        messages.success(request, _("AI-generated name and description applied."))
    else:
        messages.warning(request, _("No AI values to apply."))

    return redirect("manage:library_detail", pk=pk)

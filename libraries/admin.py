import json

from django.conf import settings
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from django.contrib.gis import admin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from libraries.geojson_import import parse_geojson
from libraries.management.commands.find_duplicates import (
    DEFAULT_RADIUS_METERS,
    find_duplicate_groups,
)
from libraries.models import (
    Favourite,
    Library,
    LibraryPhoto,
    OpenStreetMapContribution,
    OpenStreetMapContributionEvent,
    Report,
    SocialPost,
)
from libraries.notifications import (
    notify_library_approved,
    notify_library_rejected,
    notify_library_update_approved,
)
from libraries.views import GEOJSON_CACHE_KEY, HOMEPAGE_COUNT_CACHE_KEY, invalidate_cluster_cache


class OpenStreetMapStateFilter(SimpleListFilter):
    """Filter libraries by their current OpenStreetMap review state.
    Groups low-level states into the operator-facing workflow categories."""

    title = _("OpenStreetMap state")
    parameter_name = "osm_state"

    def lookups(self, request, model_admin):
        """Return the supported operator-facing OSM state filters.
        Keeps absent state distinct from known duplicate-check outcomes."""
        return [
            ("unknown", _("Unknown")),
            ("absent", _("Absent")),
            ("possible_duplicate", _("Possible duplicate")),
            ("present", _("Present")),
            ("failed", _("Failed")),
        ]

    def queryset(self, request, queryset):
        """Apply the selected OpenStreetMap state grouping.
        Includes missing contribution rows in the explicit unknown state."""
        value = self.value()
        if value == "unknown":
            return queryset.filter(
                Q(osm_contribution__isnull=True)
                | Q(
                    osm_contribution__status=(
                        OpenStreetMapContribution.Status.UNCHECKED
                    )
                )
            )
        if value == "absent":
            return queryset.filter(
                osm_contribution__status=OpenStreetMapContribution.Status.NO_MATCH
            )
        if value == "possible_duplicate":
            return queryset.filter(
                osm_contribution__status=(
                    OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
                )
            )
        if value == "present":
            return queryset.filter(
                Q(osm_contribution__osm_element_id__isnull=False)
                | Q(
                    osm_contribution__status__in=[
                        OpenStreetMapContribution.Status.ALREADY_PRESENT,
                        OpenStreetMapContribution.Status.CONTRIBUTED,
                    ]
                )
            )
        if value == "failed":
            return queryset.filter(
                osm_contribution__status=OpenStreetMapContribution.Status.FAILED
            )
        return queryset


class LibraryPhotoInline(admin.TabularInline):
    """Inline display of community photos on the Library change page."""

    model = LibraryPhoto
    extra = 0
    readonly_fields = ["photo_preview", "created_by", "created_at"]
    fields = ["photo_preview", "caption", "status", "created_by", "created_at"]

    def get_queryset(self, request):
        """Prefetch creator to avoid N+1 queries on inline display."""
        return super().get_queryset(request).select_related("created_by")

    @admin.display(description="Preview")
    def photo_preview(self, obj: LibraryPhoto) -> str:
        """Render a small thumbnail of the community photo.
        Gives admins a visual preview without leaving the library page."""
        if obj.photo:
            return format_html('<img src="{}" style="max-height:80px;">', obj.photo.url)
        return "-"


@admin.register(Library)
class LibraryAdmin(admin.GISModelAdmin):
    """Admin configuration for Library model."""

    change_list_template = "admin/libraries/library_changelist.html"
    change_form_template = "admin/libraries/library/change_form.html"
    list_display = [
        "name",
        "city",
        "country",
        "status",
        "osm_state",
        "osm_candidate",
        "created_at",
    ]
    list_filter = [
        "status",
        "country",
        "wheelchair_accessible",
        "is_indoor",
        "is_lit",
        "source",
        "brand",
        "osm_submission_allowed",
        "submission_origin",
        OpenStreetMapStateFilter,
    ]
    search_fields = ["name", "address", "city"]
    readonly_fields = [
        "slug",
        "photo_preview",
        "osm_submission_allowed",
        "osm_submission_allowed_at",
        "submission_origin",
        "osm_state_detail",
        "osm_candidate_detail",
        "osm_duplicate_candidates",
        "osm_audit_history",
        "created_at",
        "updated_at",
    ]
    autocomplete_fields = ["created_by"]
    fields = [
        "name",
        "description",
        "photo",
        "photo_preview",
        "photo_thumbnail",
        "location",
        "address",
        "city",
        "country",
        "postal_code",
        "wheelchair_accessible",
        "capacity",
        "is_indoor",
        "is_lit",
        "website",
        "contact",
        "source",
        "operator",
        "brand",
        "external_id",
        "status",
        "rejection_reason",
        "created_by",
        "osm_submission_allowed",
        "osm_submission_allowed_at",
        "submission_origin",
        "osm_state_detail",
        "osm_candidate_detail",
        "osm_duplicate_candidates",
        "osm_audit_history",
        "slug",
        "created_at",
        "updated_at",
    ]
    actions = ["approve_libraries", "reject_libraries"]
    inlines = [LibraryPhotoInline]

    def get_queryset(self, request):
        """Load related OSM state with each admin library row.
        Avoids repeated one-to-one queries in list and detail displays."""
        return super().get_queryset(request).select_related("osm_contribution")

    @admin.display(description=_("OSM state"), ordering="osm_contribution__status")
    def osm_state(self, obj: Library) -> str:
        """Return the compact operator-facing OSM state.
        Treats a missing state row as unchecked rather than an error."""
        contribution = obj._osm_contribution_or_none()
        if contribution is None:
            return str(_("Unknown"))
        return contribution.get_status_display()

    @admin.display(description=_("OSM candidate"))
    def osm_candidate(self, obj: Library) -> str:
        """Return a compact OSM eligibility indicator.
        Distinguishes base ineligibility from completed duplicate checks."""
        reason = obj.osm_precheck_ineligibility_reason()
        if reason is not None:
            return str(_("Ineligible"))
        contribution = obj._osm_contribution_or_none()
        if (
            contribution is not None
            and contribution.status == OpenStreetMapContribution.Status.NO_MATCH
        ):
            return str(_("Checked"))
        return str(_("Needs check"))

    @admin.display(description=_("OpenStreetMap state"))
    def osm_state_detail(self, obj: Library) -> str:
        """Render the current OSM state and recorded audit identifiers.
        Shows operator-safe values without exposing any credentials."""
        contribution = obj._osm_contribution_or_none()
        if contribution is None:
            return str(_("Unknown; no duplicate check has been recorded."))
        values = [str(contribution.get_status_display())]
        if contribution.checked_at is not None:
            values.append(
                str(_("Checked at: %(timestamp)s"))
                % {"timestamp": contribution.checked_at}
            )
        if contribution.osm_element_id is not None:
            values.append(
                str(_("OSM feature: %(type)s/%(identifier)s"))
                % {
                    "type": contribution.osm_element_type,
                    "identifier": contribution.osm_element_id,
                }
            )
        if contribution.changeset_id is not None:
            values.append(
                str(_("Changeset: %(identifier)s"))
                % {"identifier": contribution.changeset_id}
            )
        if contribution.last_error_message:
            values.append(
                str(_("Latest error: %(message)s"))
                % {"message": contribution.last_error_message}
            )
            values.append(
                str(_("Retryable error: %(value)s"))
                % {
                    "value": (
                        _("Yes")
                        if contribution.last_error_retryable
                        else _("No")
                    )
                }
            )
        return format_html_join("<br>", "{}", ((value,) for value in values))

    @admin.display(description=_("OpenStreetMap eligibility"))
    def osm_candidate_detail(self, obj: Library) -> str:
        """Explain the exact current OSM eligibility result.
        Uses the shared predicate intended for the future write workflow."""
        reason = obj.osm_contribution_ineligibility_reason(
            max_age_seconds=max(
                settings.OSM_DUPLICATE_CHECK_MAX_AGE_SECONDS,
                0,
            )
        )
        if reason is not None:
            return reason
        return str(
            _(
                "No library or duplicate-state blocker. Future policy and "
                "write gates still apply."
            )
        )

    @admin.display(description=_("OSM duplicate candidates"))
    def osm_duplicate_candidates(self, obj: Library) -> str:
        """Render the sanitized candidate snapshot and match signals.
        Exposes only public OSM identifiers, tags, distance, and warnings."""
        contribution = obj._osm_contribution_or_none()
        if contribution is None or not contribution.duplicate_candidates:
            return str(_("No duplicate candidates recorded."))

        rows = []
        for candidate in contribution.duplicate_candidates:
            tags = ", ".join(
                f"{key}={value}"
                for key, value in candidate.get("tags", {}).items()
            )
            signals = ", ".join(candidate.get("signals", [])) or str(
                _("secondary warning only")
            )
            rows.append((
                candidate.get("element_type", ""),
                candidate.get("element_id", ""),
                candidate.get("distance_meters", ""),
                signals,
                tags or str(_("no relevant tags")),
            ))
        return format_html(
            "<ul>{}</ul>",
            format_html_join(
                "",
                "<li><strong>{}/{}</strong> — {} m — {} — {}</li>",
                rows,
            ),
        )

    @admin.display(description=_("OSM audit history"))
    def osm_audit_history(self, obj: Library) -> str:
        """Render the latest immutable OSM audit events.
        Keeps detailed JSON out of the library form while showing provenance."""
        contribution = obj._osm_contribution_or_none()
        if contribution is None:
            return str(_("No OpenStreetMap audit events recorded."))
        events = contribution.events.select_related("actor")[:10]
        rows = [
            (
                event.created_at,
                event.get_event_type_display(),
                event.get_outcome_display(),
                str(event.actor) if event.actor is not None else str(_("System")),
            )
            for event in events
        ]
        if not rows:
            return str(_("No OpenStreetMap audit events recorded."))
        return format_html(
            "<ul>{}</ul>",
            format_html_join(
                "",
                "<li>{} — {} — {} — {}</li>",
                rows,
            ),
        )

    @admin.display(description="Photo preview")
    def photo_preview(self, obj: Library) -> str:
        """Render an inline preview of the library photo.
        Lets admins visually review the photo without clicking the file link."""
        if obj.photo:
            return format_html('<img src="{}" style="max-height:200px;">', obj.photo.url)
        return "-"

    def get_urls(self):
        """Extend admin URLs with custom management endpoints.
        Adds OSM review, imports, duplicate finder, photos, and AI views."""
        custom_urls = [
            path(
                "<path:object_id>/osm-check/",
                self.admin_site.admin_view(self.osm_check_view),
                name="libraries_library_osm_check",
            ),
            path(
                "<path:object_id>/osm-withdraw/",
                self.admin_site.admin_view(self.osm_withdraw_view),
                name="libraries_library_osm_withdraw",
            ),
            path(
                "<path:object_id>/osm-resolve/",
                self.admin_site.admin_view(self.osm_resolve_view),
                name="libraries_library_osm_resolve",
            ),
            path(
                "<path:object_id>/ai-enrich/",
                self.admin_site.admin_view(self.ai_enrich_view),
                name="libraries_library_ai_enrich",
            ),
            path(
                "<path:object_id>/ai-enrich/apply/",
                self.admin_site.admin_view(self.ai_enrich_apply_view),
                name="libraries_library_ai_enrich_apply",
            ),
            path(
                "import-geojson/",
                self.admin_site.admin_view(self.import_geojson_view),
                name="libraries_library_import_geojson",
            ),
            path(
                "find-duplicates/",
                self.admin_site.admin_view(self.find_duplicates_view),
                name="libraries_library_find_duplicates",
            ),
            path(
                "photo-grid/",
                self.admin_site.admin_view(self.photo_grid_view),
                name="libraries_library_photo_grid",
            ),
        ]
        return custom_urls + super().get_urls()

    def osm_check_view(
        self,
        request: HttpRequest,
        object_id: str,
    ) -> HttpResponse:
        """Run a read-only OSM duplicate check for one library.
        Redirects back to the protected admin detail with a clear result."""
        library = self.get_object(request=request, object_id=object_id)
        change_url = reverse(
            "admin:libraries_library_change",
            args=[object_id],
        )
        if library is None:
            messages.error(request, _("The library no longer exists."))
            return redirect("admin:libraries_library_changelist")
        if request.method != "POST":
            return redirect(change_url)

        from libraries.osm_contributions import run_osm_duplicate_check

        try:
            contribution = run_osm_duplicate_check(
                library=library,
                actor=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(change_url)

        if contribution.status == OpenStreetMapContribution.Status.FAILED:
            messages.error(
                request,
                contribution.last_error_message
                or _("The OpenStreetMap duplicate check failed."),
            )
        elif (
            contribution.status
            == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        ):
            messages.warning(
                request,
                _("Possible OpenStreetMap duplicates require staff resolution."),
            )
        else:
            messages.success(
                request,
                _("No blocking OpenStreetMap match was found."),
            )
        return redirect(change_url)

    def osm_withdraw_view(
        self,
        request: HttpRequest,
        object_id: str,
    ) -> HttpResponse:
        """Withdraw unconsumed OSM permission for one library.
        Records the staff actor in the append-only audit history."""
        library = self.get_object(request=request, object_id=object_id)
        change_url = reverse(
            "admin:libraries_library_change",
            args=[object_id],
        )
        if library is None:
            messages.error(request, _("The library no longer exists."))
            return redirect("admin:libraries_library_changelist")
        if request.method != "POST":
            return redirect(change_url)

        from libraries.osm_contributions import withdraw_osm_submission_permission

        try:
            withdraw_osm_submission_permission(
                library=library,
                actor=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("OpenStreetMap submission permission was withdrawn."),
            )
        return redirect(change_url)

    def osm_resolve_view(
        self,
        request: HttpRequest,
        object_id: str,
    ) -> HttpResponse:
        """Render and process one possible-duplicate resolution.
        Requires an explicit candidate, outcome, and staff explanation."""
        library = self.get_object(request=request, object_id=object_id)
        change_url = reverse(
            "admin:libraries_library_change",
            args=[object_id],
        )
        if library is None:
            messages.error(request, _("The library no longer exists."))
            return redirect("admin:libraries_library_changelist")

        contribution = library._osm_contribution_or_none()
        if (
            contribution is None
            or contribution.status
            != OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        ):
            messages.warning(
                request,
                _("This library has no unresolved OpenStreetMap duplicate."),
            )
            return redirect(change_url)

        if request.method == "POST":
            candidate_value = request.POST.get("candidate", "")
            resolution = request.POST.get("resolution", "")
            reason = request.POST.get("reason", "")
            try:
                element_type, raw_element_id = candidate_value.split(":", 1)
                element_id = int(raw_element_id)
            except (TypeError, ValueError):
                messages.error(
                    request,
                    _("Select a valid OpenStreetMap duplicate candidate."),
                )
            else:
                from libraries.osm_contributions import resolve_osm_duplicate

                try:
                    resolved = resolve_osm_duplicate(
                        contribution=contribution,
                        actor=request.user,
                        resolution=resolution,
                        element_type=element_type,
                        element_id=element_id,
                        reason=reason,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    if (
                        resolved.status
                        == OpenStreetMapContribution.Status.ALREADY_PRESENT
                    ):
                        messages.success(
                            request,
                            _("The existing OpenStreetMap feature was linked."),
                        )
                    elif resolution == "different_feature":
                        messages.success(
                            request,
                            _(
                                "The candidate was recorded as a different feature. "
                                "Run a fresh duplicate check before continuing."
                            ),
                        )
                    else:
                        messages.warning(
                            request,
                            _("The candidate remains an unresolved duplicate."),
                        )
                    return redirect(change_url)

        from libraries.osm_contributions import (
            get_unresolved_osm_duplicate_candidates,
        )

        context = {
            **self.admin_site.each_context(request),
            "title": _("Resolve OpenStreetMap duplicate"),
            "opts": self.model._meta,
            "library": library,
            "contribution": contribution,
            "candidates": get_unresolved_osm_duplicate_candidates(
                contribution=contribution
            ),
            "change_url": change_url,
        }
        return render(
            request,
            "admin/libraries/osm_duplicate_resolution.html",
            context,
        )

    def ai_enrich_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Generate AI name and description for a library and show confirmation.
        Calls the vision model synchronously and renders a preview page."""
        if request.method != "POST":
            return redirect(
                reverse("admin:libraries_library_change", args=[object_id])
            )

        library = Library.objects.get(pk=object_id)
        if not library.photo:
            messages.error(request, _("This library has no photo for AI analysis."))
            return redirect(
                reverse("admin:libraries_library_change", args=[object_id])
            )

        from libraries.social.image_ai import enrich_library_from_image
        from libraries.storage import get_library_photo_path

        image_path = get_library_photo_path(library)
        if not image_path:
            messages.error(request, _("Could not access the library photo."))
            return redirect(
                reverse("admin:libraries_library_change", args=[object_id])
            )

        result = enrich_library_from_image(image_path=image_path, library=library)
        if not result:
            messages.error(request, _("AI enrichment failed. Check logs for details."))
            return redirect(
                reverse("admin:libraries_library_change", args=[object_id])
            )

        context = {
            **self.admin_site.each_context(request),
            "title": _("AI Enrich: Confirm"),
            "opts": self.model._meta,
            "library": library,
            "ai_name": result["name"],
            "ai_description": result["description"],
            "apply_url": reverse(
                "admin:libraries_library_ai_enrich_apply", args=[object_id]
            ),
            "cancel_url": reverse(
                "admin:libraries_library_change", args=[object_id]
            ),
        }
        return render(request, "admin/libraries/ai_enrich_confirm.html", context)

    def ai_enrich_apply_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Apply AI-generated name and description to a library.
        Reads values from POST data to avoid a second API call."""
        if request.method != "POST":
            return redirect(
                reverse("admin:libraries_library_change", args=[object_id])
            )

        library = Library.objects.get(pk=object_id)
        ai_name = request.POST.get("ai_name", "").strip()
        ai_description = request.POST.get("ai_description", "").strip()

        update_fields: list[str] = []
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

        return redirect(
            reverse("admin:libraries_library_change", args=[object_id])
        )

    def import_geojson_view(self, request: HttpRequest) -> HttpResponse:
        """Handle GeoJSON file upload and import into Library records.
        Renders a form on GET and processes the upload on POST."""
        if request.method != "POST":
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        uploaded_file = request.FILES.get("geojson_file")
        if not uploaded_file:
            messages.error(request, "Please select a GeoJSON file to upload.")
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        try:
            raw_data = uploaded_file.read().decode("utf-8")
            geojson_data = json.loads(raw_data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            messages.error(request, f"Invalid GeoJSON file: {exc}")
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        source = request.POST.get("source", "").strip()
        status = request.POST.get("status", Library.Status.PENDING)

        if status not in (Library.Status.APPROVED, Library.Status.PENDING):
            status = Library.Status.PENDING

        candidates = parse_geojson(geojson_data)

        import tempfile

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
            f"Import of {len(candidates)} features has been queued for background processing.",
        )
        return redirect("admin:libraries_library_changelist")

    def find_duplicates_view(self, request: HttpRequest) -> HttpResponse:
        """Scan for duplicate libraries and allow bulk deletion.
        GET shows grouped duplicates; POST deletes selected entries."""
        context = {
            **self.admin_site.each_context(request),
            "title": "Find Duplicates",
            "opts": self.model._meta,
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
                cache.delete(GEOJSON_CACHE_KEY)
                cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
                invalidate_cluster_cache()
                context["deleted_count"] = deleted_count
            return render(request, "admin/libraries/find_duplicates.html", context)

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

        return render(request, "admin/libraries/find_duplicates.html", context)

    def photo_grid_view(self, request: HttpRequest) -> HttpResponse:
        """Show all photos across libraries in a resizable grid.
        Combines primary library photos and community submissions for quick review."""
        status_filter = request.GET.get("status", "all")
        type_filter = request.GET.get("type", "all")

        photos: list[dict] = []

        # Primary library photos
        if type_filter in ("all", "primary"):
            qs = Library.objects.exclude(photo="").select_related("created_by")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            for lib in qs.order_by("-created_at"):
                thumb = lib.photo_thumbnail.url if lib.photo_thumbnail else lib.photo.url
                photos.append({
                    "thumbnail_url": thumb,
                    "library_name": lib.name,
                    "library_url": reverse("admin:libraries_library_change", args=[lib.pk]),
                    "photo_type": "primary",
                    "status": lib.get_status_display(),
                    "status_raw": lib.status,
                    "submitted_by": str(lib.created_by) if lib.created_by else "",
                    "date": lib.created_at,
                })

        # Community-submitted photos
        if type_filter in ("all", "community"):
            qs = LibraryPhoto.objects.select_related("library", "created_by")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            for photo in qs.order_by("-created_at"):
                thumb = photo.photo_thumbnail.url if photo.photo_thumbnail else photo.photo.url
                photos.append({
                    "thumbnail_url": thumb,
                    "library_name": photo.library.name,
                    "library_url": reverse(
                        "admin:libraries_library_change", args=[photo.library.pk]
                    ),
                    "photo_type": "community",
                    "status": photo.get_status_display(),
                    "status_raw": photo.status,
                    "submitted_by": str(photo.created_by) if photo.created_by else "",
                    "date": photo.created_at,
                })

        # Sort combined results by date descending
        photos.sort(key=lambda p: p["date"], reverse=True)

        paginator = Paginator(photos, 60)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context = {
            **self.admin_site.each_context(request),
            "title": "Photo Grid",
            "opts": self.model._meta,
            "photos": page_obj,
            "page_obj": page_obj,
            "status_filter": status_filter,
            "type_filter": type_filter,
        }
        return render(request, "admin/libraries/photo_grid.html", context)

    @admin.action(description="Approve selected libraries")
    def approve_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Approve selected libraries and notify submitters via email.
        Applies staged updates without withdrawing approved public records."""
        libraries = list(queryset.select_related("created_by"))
        for library in libraries:
            if library.has_pending_update:
                library.apply_pending_update()
                notify_library_update_approved(library)
            elif library.status != Library.Status.APPROVED:
                was_pending = library.status == Library.Status.PENDING
                library.status = Library.Status.APPROVED
                library.save(update_fields=["status", "updated_at"])
                if was_pending:
                    notify_library_approved(library)
        count = len(libraries)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} approved."
        )

    def save_model(self, request, obj, form, change):
        """Save library and notify submitter on status transitions.
        Detects pending-to-approved and *-to-rejected transitions."""
        old_status = None
        if change and obj.pk:
            old_status = Library.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
        if not change:
            obj.osm_submission_allowed = False
            obj.osm_submission_allowed_at = None
            obj.submission_origin = Library.SubmissionOrigin.STAFF
        super().save_model(request, obj, form, change)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        if old_status == Library.Status.PENDING and obj.status == Library.Status.APPROVED:
            notify_library_approved(obj)
        if old_status != Library.Status.REJECTED and obj.status == Library.Status.REJECTED and obj.rejection_reason:
            notify_library_rejected(obj)

    @admin.action(description="Reject selected libraries")
    def reject_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Handle reject libraries.
        Supports the module workflow with a focused operation."""
        libraries = list(queryset)
        for library in libraries:
            if library.has_pending_update:
                library.discard_pending_update()
            else:
                library.status = Library.Status.REJECTED
                library.save(update_fields=["status", "updated_at"])
        count = len(libraries)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} rejected."
        )


@admin.register(OpenStreetMapContribution)
class OpenStreetMapContributionAdmin(admin.ModelAdmin):
    """Expose current OpenStreetMap state as a read-only admin record.
    Directs all transitions through the guarded Library admin workflow."""

    list_display = [
        "library",
        "status",
        "checked_at",
        "osm_element_type",
        "osm_element_id",
        "contributed_at",
    ]
    list_filter = ["status"]
    list_select_related = ["library", "contributed_by"]
    search_fields = [
        "library__name",
        "library__city",
        "osm_element_id",
        "changeset_id",
    ]
    readonly_fields = [
        "library",
        "status",
        "checked_at",
        "duplicate_candidates",
        "osm_element_type",
        "osm_element_id",
        "changeset_id",
        "contributed_at",
        "contributed_by",
        "last_attempt_key",
        "last_error_code",
        "last_error_message",
        "last_error_retryable",
    ]

    def has_add_permission(self, request):
        """Disable direct creation of current OSM state records.
        Ensures guarded checks and resolutions create the state."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable direct edits to current OSM state records.
        Keeps all state transitions inside audited service methods."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable direct deletion of current OSM state records.
        Preserves state and its linked audit history."""
        return False


@admin.register(OpenStreetMapContributionEvent)
class OpenStreetMapContributionEventAdmin(admin.ModelAdmin):
    """Expose append-only OpenStreetMap events for staff inspection.
    Prevents admin creation, editing, and deletion of audit records."""

    list_display = [
        "contribution",
        "event_type",
        "outcome",
        "actor",
        "created_at",
    ]
    list_filter = ["event_type", "outcome", "created_at"]
    list_select_related = ["contribution__library", "actor"]
    search_fields = [
        "contribution__library__name",
        "contribution__library__city",
    ]
    readonly_fields = [
        "contribution",
        "event_type",
        "outcome",
        "actor",
        "created_at",
        "attempt_key",
        "details",
    ]

    def has_add_permission(self, request):
        """Disable direct creation of OSM audit events.
        Allows only guarded service methods to append events."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable mutation of existing OSM audit events.
        Preserves the recorded operator history."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disable deletion of existing OSM audit events.
        Preserves the append-only operator history."""
        return False


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin configuration for Report model."""

    list_display = ["library", "reason", "status", "created_by", "created_at"]
    list_filter = ["status", "reason"]
    list_select_related = ["library", "created_by"]
    search_fields = ["details"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["library"]
    actions = ["resolve_reports", "dismiss_reports"]

    @admin.action(description="Resolve selected reports")
    def resolve_reports(
        self, request: HttpRequest, queryset: QuerySet[Report]
    ) -> None:
        """Handle resolve reports.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Report.Status.RESOLVED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} resolved."
        )

    @admin.action(description="Dismiss selected reports")
    def dismiss_reports(
        self, request: HttpRequest, queryset: QuerySet[Report]
    ) -> None:
        """Handle dismiss reports.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Report.Status.DISMISSED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} dismissed."
        )


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    """Admin configuration for SocialPost model with read-only fields and links."""

    list_display = ["library_link", "posted_at", "mastodon_url_short", "bluesky_url_short"]
    list_select_related = ["library"]
    readonly_fields = [
        "library_admin_link",
        "post_text",
        "posted_at",
        "mastodon_url_link",
        "bluesky_url_link",
    ]
    fields = [
        "library_admin_link",
        "post_text",
        "posted_at",
        "mastodon_url_link",
        "bluesky_url_link",
    ]

    def has_add_permission(self, request):
        """Prevent manual creation of social post records.
        Posts are created automatically by the management command."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing of social post records.
        All fields are read-only by design."""
        return False

    @admin.display(description="Library")
    def library_link(self, obj):
        """Render the library name as a link to its admin change page.
        Makes navigation between related records easy."""
        from django.urls import reverse

        url = reverse("admin:libraries_library_change", args=[obj.library.pk])
        return format_html('<a href="{}">{}</a>', url, obj.library)

    @admin.display(description="Library")
    def library_admin_link(self, obj):
        """Render the library name as a clickable admin link in detail view.
        Provides quick navigation to the parent library record."""
        from django.urls import reverse

        url = reverse("admin:libraries_library_change", args=[obj.library.pk])
        return format_html('<a href="{}">{}</a>', url, obj.library)

    @admin.display(description="Mastodon")
    def mastodon_url_short(self, obj):
        """Show a truncated Mastodon URL in the list view.
        Keeps the table compact while still showing the link."""
        if not obj.mastodon_url:
            return "-"
        short = obj.mastodon_url[:50] + "..." if len(obj.mastodon_url) > 50 else obj.mastodon_url
        return format_html('<a href="{}" target="_blank">{}</a>', obj.mastodon_url, short)

    @admin.display(description="Bluesky")
    def bluesky_url_short(self, obj):
        """Show a truncated Bluesky URL in the list view.
        Keeps the table compact while still showing the link."""
        if not obj.bluesky_url:
            return "-"
        short = obj.bluesky_url[:50] + "..." if len(obj.bluesky_url) > 50 else obj.bluesky_url
        return format_html('<a href="{}" target="_blank">{}</a>', obj.bluesky_url, short)

    @admin.display(description="Mastodon URL")
    def mastodon_url_link(self, obj):
        """Render the full Mastodon URL as a clickable link in detail view.
        Opens in a new tab for easy verification."""
        if not obj.mastodon_url:
            return "-"
        return format_html('<a href="{}" target="_blank">{}</a>', obj.mastodon_url, obj.mastodon_url)

    @admin.display(description="Bluesky URL")
    def bluesky_url_link(self, obj):
        """Render the full Bluesky URL as a clickable link in detail view.
        Opens in a new tab for easy verification."""
        if not obj.bluesky_url:
            return "-"
        return format_html('<a href="{}" target="_blank">{}</a>', obj.bluesky_url, obj.bluesky_url)


@admin.register(LibraryPhoto)
class LibraryPhotoAdmin(admin.ModelAdmin):
    """Admin configuration for LibraryPhoto model."""

    list_display = ["library", "status", "caption", "created_by", "created_at"]
    list_filter = ["status"]
    list_select_related = ["library", "created_by"]
    search_fields = ["caption"]
    readonly_fields = ["photo_preview", "created_at"]
    autocomplete_fields = ["library", "created_by"]
    fields = [
        "photo_preview",
        "photo",
        "photo_thumbnail",
        "caption",
        "status",
        "library",
        "created_by",
        "created_at",
    ]
    actions = ["approve_photos", "reject_photos"]

    @admin.display(description="Photo preview")
    def photo_preview(self, obj: LibraryPhoto) -> str:
        """Render an inline preview of the submitted photo.
        Lets admins visually review the photo for moderation."""
        if obj.photo:
            return format_html('<img src="{}" style="max-height:300px;">', obj.photo.url)
        return "-"

    @admin.action(description="Approve selected photos")
    def approve_photos(
        self, request: HttpRequest, queryset: QuerySet[LibraryPhoto]
    ) -> None:
        """Approve selected photos and promote to library primary.
        Copies the first approved photo per library as its main image."""
        photos = list(queryset.select_related("library"))
        promoted_libraries: set[int] = set()

        # Batch update all photo statuses at once
        queryset.update(status=LibraryPhoto.Status.APPROVED)

        # Promote first approved photo per library to primary
        for photo in photos:
            library = photo.library
            if library.pk not in promoted_libraries:
                library.photo = photo.photo
                library.photo_thumbnail = photo.photo_thumbnail
                library.save(update_fields=["photo", "photo_thumbnail"])
                promoted_libraries.add(library.pk)

        count = len(photos)
        self.message_user(
            request, f"{count} {'photo' if count == 1 else 'photos'} approved."
        )

    @admin.action(description="Reject selected photos")
    def reject_photos(
        self, request: HttpRequest, queryset: QuerySet[LibraryPhoto]
    ) -> None:
        """Reject selected community photos to hide them from galleries.
        Updates status so rejected photos are not publicly visible."""
        count = queryset.update(status=LibraryPhoto.Status.REJECTED)
        self.message_user(
            request, f"{count} {'photo' if count == 1 else 'photos'} rejected."
        )


@admin.register(Favourite)
class FavouriteAdmin(admin.ModelAdmin):
    """Admin configuration for user library favourites."""

    list_display = ["user", "library", "created_at"]
    list_select_related = ["user", "library"]
    autocomplete_fields = ["user", "library"]
    readonly_fields = ["created_at"]

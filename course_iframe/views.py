import csv
import logging
import math
import secrets
from functools import wraps

logger = logging.getLogger(__name__)

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    ExportFilterForm,
    HousingEntryForm,
    RelationshipFormSet,
    ResearchLoginForm,
)
from .models import Activity, ExportLog, Participant, Question, Response
from .services import (
    ActivityUnavailable,
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    housing_axis_choices,
    housing_axis_label,
    housing_numeric_fields,
    housing_raw_payloads,
    housing_scatter_config,
    housing_schema,
    palette_color_for_relationship,
    participant_count_for_question,
    relationship_color_map,
    participant_from_session,
    relationship_counts,
    relationship_display_mode,
    relationship_likert_choices,
    relationship_likert_points,
    relationship_participant_stacks,
    relationship_segmented_bars,
    remember_participant,
    save_housing_response,
    save_relationship_responses,
    update_housing_visualization,
    update_relationship_display_mode,
)


BASE_CSV_COLUMNS = [
    "participant_id",
    "activity_slug",
    "question_text",
    "question_version",
    "created_at",
    "response_language",
]

RELATIONSHIP_CSV_COLUMNS = [
    "relationship_value",
    "relationship_label",
    "income_comparison_value",
    "income_comparison_label",
    "income_likert_points",
]


def _add_payload_column(columns, column):
    if column and not column.endswith("_meta") and column not in columns:
        columns.append(column)


def housing_csv_columns(rows=None, activity_slug=HOUSING_SLUG):
    question = active_question(activity_slug, HOUSING_QUESTION_SLUG, require_active=False)
    columns = []
    for field in housing_schema(question):
        _add_payload_column(columns, field["key"])
    for item in rows or []:
        if item.activity.slug == activity_slug:
            for column in item.payload or {}:
                _add_payload_column(columns, column)
    return columns


def activity_payload_columns(slug, rows=None):
    activity = Activity.objects.filter(deleted_at__isnull=True, slug=slug).first()
    if activity and activity.activity_type == "builder":
        from .flexible import export_payload
        question = activity.questions.get(is_active=True)
        columns = list(export_payload(question=question))
        for response in rows or []:
            if response.activity_id == activity.pk:
                for key in export_payload(response):
                    _add_payload_column(columns, key)
        return columns
    if activity and activity.activity_type == "relationship_survey":
        return list(RELATIONSHIP_CSV_COLUMNS)
    if activity and activity.activity_type == "housing_survey":
        return housing_csv_columns(rows, activity_slug=slug)
    return []


def frequency_axis(max_value):
    if max_value <= 0:
        return {"max": 1, "ticks": [1, 0]}
    if max_value <= 4:
        return {"max": max_value, "ticks": list(range(max_value, -1, -1))}
    step = math.ceil(max_value / 4)
    axis_max = step * 4
    return {
        "max": axis_max,
        "ticks": [axis_max - step * index for index in range(5)],
    }


def student_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        participant = participant_from_session(request)
        if participant is None:
            return render(
                request,
                "course_iframe/session_required.html",
                status=403,
            )
        return view_func(request, participant, *args, **kwargs)

    return wrapper


def researcher_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("researcher_admin"):
            return redirect(f"{reverse('research-login')}?next={request.path}")
        return view_func(request, *args, **kwargs)

    return wrapper


def researcher_actor(request):
    return request.session.get("researcher_actor", "researcher-session:unknown")


@never_cache
@require_http_methods(["GET", "HEAD"])
def healthz(request):
    """Lightweight health check for load balancers and Docker HEALTHCHECK.

    Returns 200 only when the DB is reachable. No auth, no logging.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "fail"}, status=503)
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET", "POST"])
def activity_iframe_entry(request, activity_slug):
    # Validate access before creating an anonymous identity for a new visitor.
    if activity_slug != "summary":
        get_object_or_404(
            Activity, slug=activity_slug, is_active=True, deleted_at__isnull=True,
            activity_type__in=["housing_survey", "relationship_survey", "builder"],
        )
    language = None
    if activity_slug != "summary":
        from .localization import requested_language
        from .flexible import schema_for_question
        activity = Activity.objects.get(slug=activity_slug)
        language = requested_language(request, activity, schema_for_question(activity.questions.get(is_active=True)))
    participant = participant_from_session(request)
    if participant is None:
        from uuid import uuid4
        participant = get_or_create_participant("anon-" + uuid4().hex)
        remember_participant(request, participant)
    if activity_slug == "summary":
        return redirect("student-summary")
    from .localization import language_url
    return redirect(language_url(reverse("activity-student", args=[activity_slug]), language, activity.source_language))



@student_required
def activity_landing(request, participant):
    activities = Activity.objects.filter(deleted_at__isnull=True, is_active=True, activity_type__in=["housing_survey", "relationship_survey", "builder"]).annotate(
        saved_count=Count("responses", filter=Q(responses__participant=participant)),
    )
    return render(request, "course_iframe/activity_landing.html", {"participant": participant, "activities": activities})


def _student_question(activity_slug, question_slug):
    return get_object_or_404(
        Question.objects.select_related("activity"), activity__slug=activity_slug, activity__deleted_at__isnull=True,
        slug=question_slug, is_active=True, activity__is_active=True,
    )


def _activity_context(question):
    from .activity_builder import activity_links
    return activity_links(question.activity)


def _activity_redirect(slug, original_slug, legacy_route, route):
    return redirect(legacy_route) if slug == original_slug else redirect(route, activity_slug=slug)


@student_required
@require_http_methods(["GET", "POST"])
def relationship_activity(request, participant, activity_slug=RELATIONSHIP_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug, is_active=True)
    if activity.activity_type == "builder" or request.method == "GET" or "revision" in request.POST:
        from . import flexible_views
        return flexible_views.student(request, participant, activity)
    question = _student_question(activity_slug, RELATIONSHIP_QUESTION_SLUG)
    if request.method == "POST" and request.POST.get("question_id", str(question.pk)) != str(question.pk):
        messages.error(request, "This activity has changed. Please complete the updated form.")
        return redirect("activity-student", activity_slug=activity_slug)
    choices = question.options_json.get("choices", [])
    income_choices = relationship_likert_choices(question)
    if request.method == "POST":
        formset = RelationshipFormSet(
            request.POST,
            prefix="relationships",
            form_kwargs={"choices": choices, "income_choices": income_choices, "labels": question.options_json},
        )
        if formset.is_valid():
            try:
                save_relationship_responses(
                    participant=participant,
                    question=question,
                    relationships=formset.relationship_payloads(),
                )
            except ActivityUnavailable as exc:
                messages.error(request, str(exc))
                return redirect("activity-student", activity_slug=activity_slug)
            messages.success(request, "Responses saved.")
            return _activity_redirect(activity_slug, RELATIONSHIP_SLUG, "relationship-results", "activity-results")
    else:
        existing = Response.objects.filter(participant=participant, question=question).order_by("created_at")
        initial = []
        for response in existing:
            value = response.payload.get("relationship_value")
            initial.append(
                {
                    "relationship": value,
                    "income_comparison": response.payload.get("income_comparison_value", ""),
                    "other_relationship": response.payload.get("relationship_label", "") if value == "other" else "",
                }
            )
        formset = RelationshipFormSet(
            initial=initial,
            prefix="relationships",
            form_kwargs={"choices": choices, "income_choices": income_choices, "labels": question.options_json},
        )

    return render(
        request,
        "course_iframe/relationship_form.html",
        {
            **_activity_context(question),
            "formset": formset,
            "participant": participant,
            "question": question,
            "likert_points": relationship_likert_points(question),
        },
    )


def _relationship_chart_context(question, participant, mode=None):
    counts = relationship_counts(question)
    colors_by_value = relationship_color_map(question)
    max_count = max((item["count"] for item in counts), default=1)
    words = [
        {
            "label": item["label"],
            "count": item["count"],
            "font_size": round(1.0 + (item["count"] / max_count) * 2.4, 2),
            "color": palette_color_for_relationship(
                item["label"],
                item["is_other"],
                item["value"],
                colors_by_value,
            ),
        }
        for item in counts
    ]
    segmented = relationship_segmented_bars(question, participant=participant, mode=mode)
    axis = frequency_axis(segmented["column_max"])
    return {
        "comparison_chart_title": question.options_json.get("comparison_label", "Income comparison") + " by " + question.options_json.get("item_label", "relationship").lower(),
        "words": words,
        "segmented_columns": segmented["columns"],
        "segmented_max": segmented["column_max"],
        "segmented_axis_max": axis["max"],
        "segmented_y_ticks": axis["ticks"],
        "segmented_legend": segmented["legend"],
        "segmented_mode": segmented["mode"],
    }


@student_required
def relationship_results(request, participant, activity_slug=RELATIONSHIP_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug, is_active=True)
    from . import flexible_views
    return flexible_views.results(request, participant, activity)


@student_required
@require_http_methods(["GET", "POST"])
def housing_activity(request, participant, activity_slug=HOUSING_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug, is_active=True)
    if activity.activity_type == "builder" or request.method == "GET" or "revision" in request.POST:
        from . import flexible_views
        return flexible_views.student(request, participant, activity)
    question = _student_question(activity_slug, HOUSING_QUESTION_SLUG)
    if request.method == "POST" and request.POST.get("question_id", str(question.pk)) != str(question.pk):
        messages.error(request, "This activity has changed. Please complete the updated form.")
        return redirect("activity-student", activity_slug=activity_slug)
    if request.method == "POST":
        form = HousingEntryForm(request.POST, question=question)
        if form.is_valid():
            try:
                save_housing_response(participant, question, form.cleaned_data)
            except ActivityUnavailable as exc:
                messages.error(request, str(exc))
                return redirect("activity-student", activity_slug=activity_slug)
            messages.success(request, "Entry saved. Add another entry or view results when finished.")
            return _activity_redirect(activity_slug, HOUSING_SLUG, "housing-activity", "activity-student")
    else:
        form = HousingEntryForm(question=question)

    entries = Response.objects.filter(
        participant=participant,
        activity__slug=activity_slug,
    ).order_by("-created_at")
    summary_fields = housing_schema(question)[:2]
    return render(
        request,
        "course_iframe/housing_form.html",
        {
            **_activity_context(question),
            "form": form,
            "entries": entries,
            "participant": participant,
            "question": question,
            "summary_fields": summary_fields,
        },
    )


@student_required
@require_POST
def housing_delete_entry(request, participant, response_id):
    deleted, _ = Response.objects.filter(
        pk=response_id,
        participant=participant,
        activity__slug=HOUSING_SLUG,
        activity__deleted_at__isnull=True,
    ).delete()
    if deleted:
        messages.success(request, "Housing entry removed.")
    else:
        messages.error(request, "That entry could not be removed.")
    return redirect("housing-activity")


def _housing_chart_context(question, participant, include_identity=False):
    config = housing_scatter_config(question)
    payloads = housing_raw_payloads(question, participant, include_identity=include_identity)
    numeric_labels = {field["key"]: field["label"] for field in housing_numeric_fields(question)}
    return {
        "payloads": payloads,
        "axis_state": {"x": config["x_axis"], "y": config["y_axis"]},
        "numeric_labels": numeric_labels,
        "x_axis_label": housing_axis_label(question, config["x_axis"]),
        "y_axis_label": housing_axis_label(question, config["y_axis"]),
    }


@student_required
def housing_results(request, participant, activity_slug=HOUSING_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug, is_active=True)
    from . import flexible_views
    return flexible_views.results(request, participant, activity)


@student_required
def student_summary(request, participant):
    relationship_question = Question.objects.filter(activity__deleted_at__isnull=True, activity__slug=RELATIONSHIP_SLUG, activity__activity_type="relationship_survey", activity__is_active=True, is_active=True).select_related("activity").first()
    housing_question = Question.objects.filter(activity__deleted_at__isnull=True, activity__slug=HOUSING_SLUG, activity__activity_type="housing_survey", activity__is_active=True, is_active=True).select_related("activity").first()
    if relationship_question and "visualizations" in relationship_question.options_json:
        relationship_question = None
    if housing_question and "visualizations" in housing_question.options_json:
        housing_question = None
    relationship_context = _relationship_chart_context(relationship_question, participant) if relationship_question else {}
    housing_context = _housing_chart_context(housing_question, participant) if housing_question else {}
    return render(request, "course_iframe/student_summary.html", {
        "participant": participant, "show_relationship": bool(relationship_question), "show_housing": bool(housing_question),
        "has_your_points": any(point["is_yours"] for point in housing_context.get("payloads", [])),
        **relationship_context, **housing_context,
        "custom_activities": Activity.objects.filter(deleted_at__isnull=True,
            activity_type__in=["housing_survey", "relationship_survey", "builder"], is_active=True, responses__participant=participant,
        ).exclude(pk__in=[q.activity_id for q in [relationship_question, housing_question] if q]).distinct(),
    })


def _researcher_password_matches(submitted):
    """Verify the submitted password against either a hash or a plaintext setting.

    Prefers ``RESEARCH_ADMIN_PASSWORD_HASH`` when set (recommended for prod).
    Falls back to comparing against the plaintext ``RESEARCH_ADMIN_PASSWORD``
    for local development.
    """
    hashed = getattr(settings, "RESEARCH_ADMIN_PASSWORD_HASH", "")
    if hashed:
        return check_password(submitted, hashed)
    return constant_time_compare(submitted, settings.RESEARCH_ADMIN_PASSWORD)


@require_http_methods(["GET", "POST"])
def research_login(request):
    next_url = request.GET.get("next") or reverse("research-dashboard")
    if request.method == "POST":
        form = ResearchLoginForm(request.POST)
        if form.is_valid() and _researcher_password_matches(form.cleaned_data["password"]):
            request.session.cycle_key()
            request.session["researcher_admin"] = True
            request.session["researcher_actor"] = f"researcher-session:{secrets.token_urlsafe(8)}"
            logger.info(
                "researcher login succeeded",
                extra={
                    "event": "researcher.login.success",
                    "actor": researcher_actor(request),
                },
            )
            redirect_to = request.POST.get("next") or reverse("research-dashboard")
            if not url_has_allowed_host_and_scheme(redirect_to, allowed_hosts={request.get_host()}):
                redirect_to = reverse("research-dashboard")
            return redirect(redirect_to)
        logger.warning(
            "researcher login failed",
            extra={"event": "researcher.login.failed", "actor": request.META.get("REMOTE_ADDR", "")},
        )
        messages.error(request, "The password was not accepted.")
    else:
        form = ResearchLoginForm()

    return render(
        request,
        "research/login.html",
        {"form": form, "next": next_url},
    )


def research_logout(request):
    request.session.pop("researcher_admin", None)
    request.session.pop("researcher_actor", None)
    return redirect("research-login")


@researcher_required
def research_dashboard(request):
    from .activity_builder import ACTIVITY_TYPES, activity_links
    cards = []
    for activity in Activity.objects.filter(deleted_at__isnull=True, activity_type__in=ACTIVITY_TYPES).annotate(
        response_count=Count("responses", filter=Q(responses__question__is_active=True)),
        participant_count=Count("responses__participant", distinct=True, filter=Q(responses__question__is_active=True)),
    ):
        cards.append({**activity_links(activity), "count": activity.response_count,
            "participant_count": activity.participant_count, "format_label": ACTIVITY_TYPES[activity.activity_type]["label"]})
    return render(request, "research/dashboard.html", {
        "participant_count": Participant.objects.count(), "activity_cards": cards,
        "export_form": ExportFilterForm(), "recent_exports": ExportLog.objects.all()[:8],
    })


@researcher_required
@require_http_methods(["GET", "POST"])
def research_relationship_plots(request, activity_slug=RELATIONSHIP_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug)
    if activity.activity_type == "builder" or request.method != "POST" or "display_mode" not in request.POST:
        from . import flexible_views
        return flexible_views.plots(request, activity)
    question = active_question(activity_slug, RELATIONSHIP_QUESTION_SLUG, require_active=False)
    if request.method == "POST":
        previous_mode = relationship_display_mode(question)
        new_mode = (request.POST.get("display_mode") or "").strip()
        updated = update_relationship_display_mode(new_mode, question=question)
        applied = relationship_display_mode(updated)
        if applied == previous_mode:
            messages.info(request, "No changes to relationship chart.")
        else:
            messages.success(request, "Relationship chart updated.")
            logger.info(
                "relationship display mode updated",
                extra={
                    "event": "researcher.relationship.display.updated",
                    "activity": RELATIONSHIP_SLUG,
                    "actor": researcher_actor(request),
                    "mode": applied,
                },
            )
        return _activity_redirect(activity_slug, RELATIONSHIP_SLUG, "research-relationship-plots", "research-activity-plots")

    count = participant_count_for_question(question)
    chart_context = _relationship_chart_context(question, participant=None)
    participant_stacks = relationship_participant_stacks(question)
    return render(
        request,
        "course_iframe/relationship_results.html",
        {
            **_activity_context(question),
            "count": count,
            "display_mode_choices": [
                ("both", "Side-by-side (yours + cohort)"),
                ("yours", "Yours only"),
                ("cohort", "Cohort only"),
            ],
            "is_research_view": True,
            "participant_stacks": participant_stacks,
            "focus_choices": sorted(participant_stacks.keys()),
            **chart_context,
        },
    )


def _parse_axis_spec(post, prefix):
    mode = post.get(f"{prefix}_mode", "single")
    numerator = (post.get(f"{prefix}_numerator") or "").strip()
    denominator = (post.get(f"{prefix}_denominator") or "").strip() if mode == "ratio" else ""
    label = (post.get(f"{prefix}_label") or "").strip() if mode == "ratio" else ""
    return {"numerator": numerator, "denominator": denominator, "label": label}


@researcher_required
@require_http_methods(["GET", "POST"])
def research_housing_plots(request, activity_slug=HOUSING_SLUG):
    activity = get_object_or_404(Activity, deleted_at__isnull=True, slug=activity_slug)
    if activity.activity_type == "builder" or request.method != "POST" or "x_axis_mode" not in request.POST:
        from . import flexible_views
        return flexible_views.plots(request, activity)
    question = active_question(activity_slug, HOUSING_QUESTION_SLUG, require_active=False)
    if request.method == "POST":
        old_config = housing_scatter_config(question)
        x_axis = _parse_axis_spec(request.POST, "x_axis")
        y_axis = _parse_axis_spec(request.POST, "y_axis")
        updated = update_housing_visualization(x_axis=x_axis, y_axis=y_axis, question=question)
        new_config = housing_scatter_config(updated)
        if new_config == old_config:
            messages.info(request, "No changes to scatter plot.")
        else:
            messages.success(request, "Scatter plot axes updated.")
            logger.info(
                "housing scatter plot settings updated",
                extra={
                    "event": "researcher.housing.visualization.updated",
                    "activity": HOUSING_SLUG,
                    "actor": researcher_actor(request),
                    "x_axis": new_config["x_axis"],
                    "y_axis": new_config["y_axis"],
                },
            )
        return _activity_redirect(activity_slug, HOUSING_SLUG, "research-housing-plots", "research-activity-plots")

    count = participant_count_for_question(question)
    chart_context = _housing_chart_context(question, None, include_identity=True)
    numeric_choices = housing_axis_choices(question)
    focus_choices = sorted(
        {row["participant_id"] for row in chart_context["payloads"] if row.get("participant_id")}
    )
    return render(
        request,
        "course_iframe/housing_results.html",
        {
            **_activity_context(question),
            "count": count,
            "has_your_points": False,
            "focus_choices": focus_choices,
            "numeric_choices": [{"key": key, "label": label} for key, label in numeric_choices],
            "axis_builder_rows": [
                ("x_axis", "X axis", chart_context["axis_state"]["x"]),
                ("y_axis", "Y axis", chart_context["axis_state"]["y"]),
            ],
            "is_research_view": True,
            **chart_context,
        },
    )


@researcher_required
@require_http_methods(["GET", "POST"])
def edit_relationship(request):
    from .builder_views import activity_editor
    return activity_editor(request, activity_slug=RELATIONSHIP_SLUG)


@researcher_required
@require_http_methods(["GET", "POST"])
def edit_housing(request):
    from .builder_views import activity_editor
    return activity_editor(request, activity_slug=HOUSING_SLUG)


@researcher_required
def research_export_csv(request):
    form = ExportFilterForm(request.GET)
    if not form.is_valid():
        return HttpResponse("Invalid export filters", status=400, content_type="text/plain")

    qs = Response.objects.filter(activity__deleted_at__isnull=True).select_related(
        "participant",
        "activity",
        "question",
    ).order_by("created_at")
    activity = form.cleaned_data.get("activity")
    date_start = form.cleaned_data.get("date_start")
    date_end = form.cleaned_data.get("date_end")
    if activity:
        qs = qs.filter(activity__slug=activity)
    if date_start:
        qs = qs.filter(created_at__date__gte=date_start)
    if date_end:
        qs = qs.filter(created_at__date__lte=date_end)

    rows = list(qs)
    if activity:
        payload_columns = activity_payload_columns(activity, rows)
    else:
        payload_columns = []
        for slug in dict.fromkeys([RELATIONSHIP_SLUG, HOUSING_SLUG] + [row.activity.slug for row in rows]):
            for column in activity_payload_columns(slug, rows):
                if column not in payload_columns:
                    payload_columns.append(column)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="elearning-responses.csv"'
    writer = csv.writer(response)
    writer.writerow(BASE_CSV_COLUMNS + payload_columns)
    row_count = 0
    for item in rows:
        payload = item.payload or {}
        if item.activity.activity_type == "builder":
            from .flexible import export_payload
            payload = export_payload(item)
        writer.writerow(
            [
                item.participant.participant_id,
                item.activity.slug,
                item.display_json.get("activity_title", item.question.question_text),
                item.question_version,
                item.created_at.isoformat(),
                item.language,
            ]
            + [payload.get(column, "") for column in payload_columns]
        )
        row_count += 1

    ExportLog.objects.create(
        export_type="responses",
        file_format="csv",
        activity_slug=activity or "",
        date_start=date_start,
        date_end=date_end,
        row_count=row_count,
    )
    logger.info(
        "csv export generated",
        extra={
            "event": "researcher.export.csv",
            "activity": activity or "all",
            "actor": researcher_actor(request),
            "row_count": row_count,
        },
    )
    return response

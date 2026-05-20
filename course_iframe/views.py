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
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    ExportFilterForm,
    HousingEditForm,
    HousingEntryForm,
    HousingFieldFormSetFactory,
    ParticipantEntryForm,
    RelationshipEditForm,
    RelationshipFormSet,
    ResearchLoginForm,
)
from .models import ExportLog, Participant, Response
from .services import (
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    housing_axis_label,
    housing_scatter_config,
    housing_scatter_points,
    housing_schema,
    mvp_activities,
    palette_color_for_relationship,
    participant_count_for_question,
    relationship_color_map,
    participant_from_session,
    relationship_counts,
    relationship_likert_choices,
    relationship_likert_points,
    relationship_segmented_bars,
    remember_participant,
    save_housing_response,
    save_relationship_responses,
    update_housing_schema,
    update_housing_visualization,
    update_relationship_settings,
    valid_iframe_signature,
)


ACTIVITY_ENTRY_TARGETS = {
    RELATIONSHIP_SLUG: {
        "title": "Relationship survey",
        "redirect": "relationship-activity",
    },
    HOUSING_SLUG: {
        "title": "Housing data entry",
        "redirect": "housing-activity",
    },
}

BASE_CSV_COLUMNS = [
    "participant_id",
    "activity_slug",
    "question_text",
    "question_version",
    "created_at",
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


def housing_csv_columns(rows=None):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    columns = []
    for field in housing_schema(question):
        _add_payload_column(columns, field["key"])
    if rows is not None:
        for item in rows:
            if item.activity.slug != HOUSING_SLUG:
                continue
            for column in (item.payload or {}):
                _add_payload_column(columns, column)
    return columns


def activity_payload_columns(slug, rows=None):
    if slug == RELATIONSHIP_SLUG:
        return list(RELATIONSHIP_CSV_COLUMNS)
    if slug == HOUSING_SLUG:
        return housing_csv_columns(rows)
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


@require_http_methods(["GET"])
def iframe_entry(request):
    course_id = request.GET.get("course_id", "").strip()
    participant_id = request.GET.get("participant_id", "").strip()
    signature = request.GET.get("sig", "").strip()
    if not valid_iframe_signature(course_id, participant_id, signature):
        return render(
            request,
            "course_iframe/iframe_error.html",
            {"course_id": course_id, "participant_id": participant_id},
            status=403,
        )

    participant = get_or_create_participant(participant_id)
    remember_participant(request, participant)
    return redirect("activity-landing")


@require_http_methods(["GET", "POST"])
def activity_iframe_entry(request, activity_slug):
    activity_config = ACTIVITY_ENTRY_TARGETS.get(activity_slug)
    if activity_config is None:
        raise Http404("Activity not found")

    data_source = request.POST if request.method == "POST" else request.GET
    initial = {
        "pseudoname": (
            data_source.get("pseudoname")
            or data_source.get("participant_id")
            or ""
        ).strip(),
    }
    if request.method == "POST":
        form = ParticipantEntryForm(request.POST)
        if form.is_valid():
            participant = get_or_create_participant(form.cleaned_data["pseudoname"])
            remember_participant(request, participant)
            return redirect(activity_config["redirect"])
    else:
        form = ParticipantEntryForm(initial=initial)

    return render(
        request,
        "course_iframe/participant_entry.html",
        {
            "activity_title": activity_config["title"],
            "form": form,
        },
    )


@student_required
def activity_landing(request, participant):
    relationship_done = Response.objects.filter(
        participant=participant,
        activity__slug=RELATIONSHIP_SLUG,
    ).exists()
    housing_count = Response.objects.filter(
        participant=participant,
        activity__slug=HOUSING_SLUG,
    ).count()
    return render(
        request,
        "course_iframe/activity_landing.html",
        {
            "participant": participant,
            "activities": mvp_activities(),
            "relationship_done": relationship_done,
            "housing_count": housing_count,
        },
    )


@student_required
@require_http_methods(["GET", "POST"])
def relationship_activity(request, participant):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    choices = question.options_json.get("choices", [])
    income_choices = relationship_likert_choices(question)
    if request.method == "POST":
        formset = RelationshipFormSet(
            request.POST,
            prefix="relationships",
            form_kwargs={"choices": choices, "income_choices": income_choices},
        )
        if formset.is_valid():
            save_relationship_responses(
                participant=participant,
                question=question,
                relationships=formset.relationship_payloads(),
            )
            messages.success(request, "Relationship responses saved.")
            return redirect("relationship-results")
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
            form_kwargs={"choices": choices, "income_choices": income_choices},
        )

    return render(
        request,
        "course_iframe/relationship_form.html",
        {
            "formset": formset,
            "participant": participant,
            "question": question,
            "likert_points": relationship_likert_points(question),
        },
    )


@student_required
def relationship_results(request, participant):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    count = participant_count_for_question(question)
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
    segmented = relationship_segmented_bars(question)
    axis = frequency_axis(segmented["column_max"])
    return render(
        request,
        "course_iframe/relationship_results.html",
        {
            "participant": participant,
            "count": count,
            "words": words,
            "segmented_columns": segmented["columns"],
            "segmented_max": segmented["column_max"],
            "segmented_axis_max": axis["max"],
            "segmented_y_ticks": axis["ticks"],
            "segmented_legend": segmented["legend"],
        },
    )


@student_required
@require_http_methods(["GET", "POST"])
def housing_activity(request, participant):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    if request.method == "POST":
        form = HousingEntryForm(request.POST, question=question)
        if form.is_valid():
            save_housing_response(participant, question, form.cleaned_data)
            messages.success(request, "Housing entry saved. Add another entry or view results when finished.")
            return redirect("housing-activity")
    else:
        form = HousingEntryForm(question=question)

    entries = Response.objects.filter(
        participant=participant,
        activity__slug=HOUSING_SLUG,
    ).order_by("-created_at")
    summary_fields = housing_schema(question)[:2]
    return render(
        request,
        "course_iframe/housing_form.html",
        {
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
    ).delete()
    if deleted:
        messages.success(request, "Housing entry removed.")
    else:
        messages.error(request, "That entry could not be removed.")
    return redirect("housing-activity")


@student_required
def housing_results(request, participant):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    config = housing_scatter_config(question)
    count = participant_count_for_question(question)
    points = housing_scatter_points(
        question,
        participant,
        config["x_axis"],
        config["y_axis"],
    )
    return render(
        request,
        "course_iframe/housing_results.html",
        {
            "participant": participant,
            "count": count,
            "points": points,
            "has_your_points": any(point["is_yours"] for point in points),
            "x_axis_label": housing_axis_label(question, config["x_axis"]),
            "y_axis_label": housing_axis_label(question, config["y_axis"]),
            "x_axis_key": config["x_axis"],
            "y_axis_key": config["y_axis"],
        },
    )


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
    active_questions = {
        RELATIONSHIP_SLUG: active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG),
        HOUSING_SLUG: active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG),
    }
    edit_routes = {
        RELATIONSHIP_SLUG: "research-edit-relationship",
        HOUSING_SLUG: "research-edit-housing",
    }
    plot_routes = {
        RELATIONSHIP_SLUG: "research-relationship-plots",
        HOUSING_SLUG: "research-housing-plots",
    }
    activity_cards = []
    for activity in mvp_activities():
        slug = activity.slug
        activity_cards.append(
            {
                "slug": slug,
                "title": activity.title,
                "count": Response.objects.filter(question=active_questions[slug]).count(),
                "edit_url_name": edit_routes.get(slug),
                "plot_url_name": plot_routes.get(slug),
            }
        )

    return render(
        request,
        "research/dashboard.html",
        {
            "participant_count": Participant.objects.count(),
            "activity_cards": activity_cards,
            "export_form": ExportFilterForm(),
            "recent_exports": ExportLog.objects.all()[:8],
        },
    )


@researcher_required
def research_relationship_plots(request):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    count = participant_count_for_question(question)
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
    segmented = relationship_segmented_bars(question)
    axis = frequency_axis(segmented["column_max"])
    return render(
        request,
        "course_iframe/relationship_results.html",
        {
            "count": count,
            "words": words,
            "segmented_columns": segmented["columns"],
            "segmented_max": segmented["column_max"],
            "segmented_axis_max": axis["max"],
            "segmented_y_ticks": axis["ticks"],
            "segmented_legend": segmented["legend"],
            "is_research_view": True,
        },
    )


@researcher_required
def research_housing_plots(request):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    config = housing_scatter_config(question)
    count = participant_count_for_question(question)
    points = housing_scatter_points(
        question,
        None,
        config["x_axis"],
        config["y_axis"],
    )
    return render(
        request,
        "course_iframe/housing_results.html",
        {
            "count": count,
            "points": points,
            "has_your_points": False,
            "x_axis_label": housing_axis_label(question, config["x_axis"]),
            "y_axis_label": housing_axis_label(question, config["y_axis"]),
            "x_axis_key": config["x_axis"],
            "y_axis_key": config["y_axis"],
            "is_research_view": True,
        },
    )


@researcher_required
@require_http_methods(["GET", "POST"])
def edit_relationship(request):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    form = RelationshipEditForm(request.POST or None, question=question)
    if request.method == "POST" and form.is_valid():
        if not form.has_meaningful_changes():
            messages.info(request, "No changes to save.")
            return redirect("research-edit-relationship")
        new_question = update_relationship_settings(
            question_text=form.cleaned_data["question_text"],
            likert_points=int(form.cleaned_data["likert_points"]),
            likert_labels=form.labels_for_selected_scale(),
            relationship_choices=form.relationship_choices_for_save(),
        )
        logger.info(
            "relationship survey updated",
            extra={
                "event": "researcher.schema.relationship.updated",
                "activity": RELATIONSHIP_SLUG,
                "actor": researcher_actor(request),
                "from_version": question.version,
                "to_version": new_question.version,
            },
        )
        messages.success(request, "Relationship survey updated.")
        return redirect("research-edit-relationship")

    return render(
        request,
        "research/edit_relationship.html",
        {
            "form": form,
            "question": question,
            "selected_points": int(form["likert_points"].value() or relationship_likert_points(question)),
        },
    )


@researcher_required
@require_http_methods(["GET", "POST"])
def edit_housing(request):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    form = HousingEditForm(request.POST or None, question=question)
    if request.method == "POST":
        field_formset = HousingFieldFormSetFactory(request.POST, prefix="fields")
    else:
        field_formset = HousingFieldFormSetFactory(
            initial=form.initial_field_rows(),
            prefix="fields",
        )

    if request.method == "POST" and form.is_valid() and field_formset.is_valid():
        new_fields = form.serialize_fields(field_formset)
        if not new_fields:
            messages.error(request, "Add at least one housing field.")
        elif not form.has_meaningful_changes(new_fields):
            messages.info(request, "No changes to save.")
            return redirect("research-edit-housing")
        elif form.schema_has_meaningful_changes(new_fields):
            new_question = update_housing_schema(
                question_text=form.cleaned_data["question_text"],
                fields=new_fields,
                viz={
                    "x_axis": form.cleaned_data["x_axis"],
                    "y_axis": form.cleaned_data["y_axis"],
                },
            )
            logger.info(
                "housing schema updated",
                extra={
                    "event": "researcher.schema.housing.updated",
                    "activity": HOUSING_SLUG,
                    "actor": researcher_actor(request),
                    "from_version": question.version,
                    "to_version": new_question.version,
                },
            )
            messages.success(request, "Housing activity updated.")
            return redirect("research-edit-housing")
        else:
            updated_question = update_housing_visualization(
                x_axis=form.cleaned_data["x_axis"],
                y_axis=form.cleaned_data["y_axis"],
            )
            logger.info(
                "housing scatter plot settings updated",
                extra={
                    "event": "researcher.housing.visualization.updated",
                    "activity": HOUSING_SLUG,
                    "actor": researcher_actor(request),
                    "from_version": question.version,
                    "to_version": updated_question.version,
                },
            )
            messages.success(request, "Housing scatter plot updated.")
            return redirect("research-edit-housing")

    return render(
        request,
        "research/edit_housing.html",
        {
            "form": form,
            "field_formset": field_formset,
            "question": question,
        },
    )


@researcher_required
def research_export_csv(request):
    form = ExportFilterForm(request.GET)
    if not form.is_valid():
        return HttpResponse("Invalid export filters", status=400, content_type="text/plain")

    qs = Response.objects.select_related(
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
        for slug in (RELATIONSHIP_SLUG, HOUSING_SLUG):
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
        writer.writerow(
            [
                item.participant.participant_id,
                item.activity.slug,
                item.question.question_text,
                item.question_version,
                item.created_at.isoformat(),
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

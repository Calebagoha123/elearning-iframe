import csv
import json
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import ExportFilterForm, HousingEntryForm, RelationshipFormSet, ResearchLoginForm
from .models import ExportLog, Participant, Response
from .services import (
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    housing_comparison_rows,
    mvp_activities,
    participant_count_for_course_activity,
    participant_from_session,
    relationship_counts,
    remember_participant,
    save_housing_response,
    save_relationship_responses,
    valid_iframe_signature,
)


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

    participant = get_or_create_participant(course_id, participant_id)
    remember_participant(request, participant)
    return redirect("activity-landing")


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
    if request.method == "POST":
        formset = RelationshipFormSet(
            request.POST,
            prefix="relationships",
            form_kwargs={"choices": choices},
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
                    "other_relationship": response.payload.get("relationship_label", "") if value == "other" else "",
                }
            )
        formset = RelationshipFormSet(
            initial=initial,
            prefix="relationships",
            form_kwargs={"choices": choices},
        )

    return render(
        request,
        "course_iframe/relationship_form.html",
        {"formset": formset, "participant": participant, "question": question},
    )


@student_required
def relationship_results(request, participant):
    count = participant_count_for_course_activity(participant.course, RELATIONSHIP_SLUG)
    threshold = settings.VISUALIZATION_MIN_RESPONSES
    if count < threshold:
        return render(
            request,
            "course_iframe/locked_state.html",
            {
                "title": "Relationship results",
                "count": count,
                "threshold": threshold,
                "continue_url": reverse("activity-landing"),
            },
        )

    counts = relationship_counts(participant.course)
    max_count = max((item[1] for item in counts), default=1)
    words = [
        {
            "label": label,
            "count": item_count,
            "font_size": round(1.0 + (item_count / max_count) * 2.4, 2),
        }
        for label, item_count in counts
    ]
    return render(
        request,
        "course_iframe/relationship_results.html",
        {
            "participant": participant,
            "count": count,
            "words": words,
        },
    )


@student_required
@require_http_methods(["GET", "POST"])
def housing_activity(request, participant):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    if request.method == "POST":
        form = HousingEntryForm(request.POST)
        if form.is_valid():
            save_housing_response(participant, question, form.cleaned_data)
            messages.success(request, "Housing entry saved.")
            return redirect("housing-activity")
    else:
        form = HousingEntryForm()

    entries = Response.objects.filter(
        participant=participant,
        activity__slug=HOUSING_SLUG,
    ).order_by("-created_at")
    return render(
        request,
        "course_iframe/housing_form.html",
        {
            "form": form,
            "entries": entries,
            "participant": participant,
            "question": question,
        },
    )


@student_required
def housing_results(request, participant):
    count = participant_count_for_course_activity(participant.course, HOUSING_SLUG)
    threshold = settings.VISUALIZATION_MIN_RESPONSES
    if count < threshold:
        return render(
            request,
            "course_iframe/locked_state.html",
            {
                "title": "Housing comparison",
                "count": count,
                "threshold": threshold,
                "continue_url": reverse("housing-activity"),
            },
        )

    return render(
        request,
        "course_iframe/housing_results.html",
        {
            "participant": participant,
            "count": count,
            "rows": housing_comparison_rows(participant.course, participant),
        },
    )


@require_http_methods(["GET", "POST"])
def research_login(request):
    next_url = request.GET.get("next") or reverse("research-dashboard")
    if request.method == "POST":
        form = ResearchLoginForm(request.POST)
        if form.is_valid() and form.cleaned_data["password"] == settings.RESEARCH_ADMIN_PASSWORD:
            request.session["researcher_admin"] = True
            redirect_to = request.POST.get("next") or reverse("research-dashboard")
            if not url_has_allowed_host_and_scheme(redirect_to, allowed_hosts={request.get_host()}):
                redirect_to = reverse("research-dashboard")
            return redirect(redirect_to)
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
    return redirect("research-login")


@researcher_required
def research_dashboard(request):
    activity_counts = (
        Response.objects.values("activity__slug", "activity__title")
        .annotate(count=Count("id"))
        .order_by("activity__slug")
    )
    return render(
        request,
        "research/dashboard.html",
        {
            "participant_count": Participant.objects.count(),
            "response_count": Response.objects.count(),
            "activity_counts": activity_counts,
            "export_form": ExportFilterForm(),
            "recent_exports": ExportLog.objects.all()[:8],
        },
    )


@researcher_required
def research_export_csv(request):
    form = ExportFilterForm(request.GET)
    if not form.is_valid():
        return HttpResponse("Invalid export filters", status=400, content_type="text/plain")

    qs = Response.objects.select_related(
        "participant",
        "participant__course",
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

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="elearning-responses.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "course_id",
            "participant_id",
            "activity_slug",
            "question_text",
            "question_version",
            "payload",
            "created_at",
        ]
    )
    row_count = 0
    for item in qs:
        writer.writerow(
            [
                item.participant.course.course_id,
                item.participant.participant_id,
                item.activity.slug,
                item.question.question_text,
                item.question_version,
                json.dumps(item.payload, sort_keys=True),
                item.created_at.isoformat(),
            ]
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
    return response

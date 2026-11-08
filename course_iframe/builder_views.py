"""Create, configure, preview, and review every course activity through one catalog."""

import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_http_methods

from .activity_builder import (
    ACTIVITY_TYPES,
    ActivityChanged,
    ActivitySettingsForm,
    NewActivityForm,
    activity_links,
    activity_question,
)
from .forms import (
    HousingEditForm,
    HousingFieldFormSetFactory,
    RelationshipEditForm,
)
from .models import Activity, Response
from .services import (
    relationship_likert_points,
    update_housing_schema,
    update_relationship_settings,
)
from . import views

logger = logging.getLogger(__name__)


def get_activity(slug, published=False):
    filters = {
        "slug": slug,
        "activity_type__in": ACTIVITY_TYPES,
        "deleted_at__isnull": True,
    }
    if published:
        filters["is_active"] = True
    return get_object_or_404(Activity, **filters)


@views.researcher_required
@require_http_methods(["GET", "POST"])
def new_activity(request):
    form = NewActivityForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        activity = form.save()
        messages.success(
            request,
            "Activity created as a draft. Configure its questions and publish when ready.",
        )
        return redirect("research-edit-activity", activity_slug=activity.slug)
    return render(
        request,
        "research/new_activity.html",
        {"form": form, "activity_types": ACTIVITY_TYPES},
    )


@views.researcher_required
@require_http_methods(["GET", "POST"])
def activity_editor(request, activity_slug):
    from . import flexible_views

    activity = get_activity(activity_slug)
    if (
        request.method == "POST"
        and "question_text" in request.POST
        and "schema" not in request.POST
        and activity.activity_type != "builder"
    ):
        return legacy_activity_editor(request, activity_slug)
    return flexible_views.editor(request, activity)


@views.researcher_required
@require_http_methods(["GET", "POST"])
def legacy_activity_editor(request, activity_slug):
    activity = get_activity(activity_slug)
    question = activity_question(activity)
    is_housing = activity.activity_type == "housing_survey"
    data = request.POST if request.method == "POST" else None
    form = (HousingEditForm if is_housing else RelationshipEditForm)(
        data, question=question
    )
    # Legacy editor posts omit metadata; real forms submit the whole activity.
    settings_data = data if data is not None and "activity_settings" in data else None
    details = ActivitySettingsForm(settings_data, instance=activity, prefix="activity")
    formset = None
    if is_housing:
        formset = HousingFieldFormSetFactory(
            data,
            prefix="fields",
            initial=form.initial_field_rows() if data is None else None,
        )
    if data is not None:
        valid = form.is_valid()
        if formset is not None:
            valid = formset.is_valid() and valid
        if settings_data is not None:
            valid = details.is_valid() and valid
        fields = form.serialize_fields(formset) if valid and is_housing else None
        if valid and is_housing and not fields:
            form.add_error(None, "Add at least one field.")
            valid = False
        if valid:
            try:
                with transaction.atomic():
                    current = Activity.objects.select_for_update().get(pk=activity.pk)
                    if (
                        current.deleted_at
                        or not current.questions.filter(
                            pk=question.pk, is_active=True
                        ).exists()
                        or (
                            settings_data is not None
                            and current.revision != details.cleaned_data["revision"]
                        )
                    ):
                        raise ActivityChanged(
                            "Someone saved changes while you were editing. Reload before saving again."
                        )
                    question.refresh_from_db(fields=["options_json"])
                    changed = (
                        form.has_meaningful_changes(fields)
                        if is_housing
                        else form.has_meaningful_changes()
                    )
                    if changed:
                        if is_housing:
                            update_housing_schema(
                                form.cleaned_data["question_text"],
                                fields,
                                question=question,
                            )
                        else:
                            update_relationship_settings(
                                form.cleaned_data["question_text"],
                                int(form.cleaned_data["likert_points"]),
                                form.labels_for_selected_scale(),
                                form.relationship_choices_for_save(),
                                question=question,
                                input_labels=form.input_labels_for_save(),
                            )
                    if settings_data is not None:
                        activity = details.save(commit=False)
                    else:
                        activity = current
                    activity.revision = current.revision + 1
                    activity.save()
            except ActivityChanged as exc:
                details.add_error(None, str(exc))
            else:
                logger.info(
                    "activity updated",
                    extra={
                        "event": "researcher.activity.updated",
                        "activity": activity.slug,
                        "actor": views.researcher_actor(request),
                    },
                )
                messages.success(
                    request,
                    "Activity saved."
                    if changed or settings_data is not None
                    else "No changes to save.",
                )
                return redirect(request.path)
    embed_url = request.build_absolute_uri(
        reverse("activity-iframe-entry", args=[activity.slug])
    )
    embed_code = '<iframe src="{}" title="{}" width="100%" height="800" style="border:0" loading="lazy"></iframe>'.format(
        escape(embed_url), escape(activity.title)
    )
    return render(
        request,
        ACTIVITY_TYPES[activity.activity_type]["edit_template"],
        {
            **activity_links(activity),
            "form": form,
            "field_formset": formset,
            "question": question,
            "activity_settings": details,
            "embed_url": embed_url,
            "embed_code": embed_code,
            "selected_points": int(
                form["likert_points"].value() or relationship_likert_points(question)
            )
            if not is_housing
            else None,
        },
    )


@views.researcher_required
@require_http_methods(["GET"])
def activity_preview(request, activity_slug):
    from . import flexible_views

    return flexible_views.preview(request, get_activity(activity_slug))


@views.student_required
@require_http_methods(["GET", "POST"])
def student_activity(request, participant, activity_slug):
    activity = get_activity(activity_slug, published=True)
    if activity.activity_type == "builder":
        from . import flexible_views

        return flexible_views.student(request, participant, activity)
    view = (
        views.housing_activity
        if activity.activity_type == "housing_survey"
        else views.relationship_activity
    )
    return view(request, activity_slug=activity.slug)


@views.student_required
@require_http_methods(["GET"])
def student_results(request, participant, activity_slug):
    activity = get_activity(activity_slug, published=True)
    if activity.activity_type == "builder":
        from . import flexible_views

        return flexible_views.results(request, participant, activity)
    view = (
        views.housing_results
        if activity.activity_type == "housing_survey"
        else views.relationship_results
    )
    return view(request, activity_slug=activity.slug)


@views.researcher_required
@require_http_methods(["GET", "POST"])
def activity_plots(request, activity_slug):
    from . import flexible_views

    return flexible_views.plots(request, get_activity(activity_slug))


@views.student_required
@require_http_methods(["POST"])
def delete_entry(request, participant, activity_slug, response_id):
    activity = get_activity(activity_slug, published=True)
    get_object_or_404(
        Response, pk=response_id, activity=activity, participant=participant
    ).delete()
    messages.success(request, "Entry removed.")
    return redirect("activity-student", activity_slug=activity.slug)


@views.researcher_required
@require_http_methods(["GET"])
def activity_responses(request, activity_slug):
    activity = get_activity(activity_slug)
    question = activity_question(activity)
    all_versions = request.GET.get("versions") == "all"
    responses = activity.responses.select_related("participant", "question").order_by(
        "-created_at"
    )
    if not all_versions:
        responses = responses.filter(question=question)
    search = request.GET.get("participant", "").strip()
    if search:
        responses = responses.filter(participant__participant_id__icontains=search)
    page = Paginator(responses, 50).get_page(request.GET.get("page"))
    from .flexible import response_answers

    rows = [
        {"response": response, "answers": response_answers(response)}
        for response in page
    ]
    return render(
        request,
        "research/activity_responses.html",
        {
            **activity_links(activity),
            "rows": rows,
            "page": page,
            "all_versions": all_versions,
            "participant_search": search,
            "question": question,
        },
    )


@views.researcher_required
@require_http_methods(["POST"])
@transaction.atomic
def delete_activity(request, activity_slug):
    activity = get_object_or_404(
        Activity.objects.select_for_update(),
        slug=activity_slug,
        activity_type__in=ACTIVITY_TYPES,
        deleted_at__isnull=True,
    )
    activity.deleted_at = timezone.now()
    activity.revision += 1
    activity.save(update_fields=["deleted_at", "revision"])
    return redirect("research-dashboard")

"""The common editor and student experience for all activity question combinations."""

import json
from copy import copy

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape

from .activity_builder import ActivitySettingsForm, activity_links, activity_question
from .flexible import (
    BUILDER,
    SCHEMA_KEY,
    FIELD_TYPES,
    SchemaEditorForm,
    current_question,
    response_answers,
    save_entries,
    schema_for_question,
    section_formsets,
    version_questions,
)
from .flexible_plots import (
    PlotSettingsForm,
    default_plot_settings,
    plot_context,
    field_catalog,
)
from .models import Activity, Question, Response, Participant


def editor(request, activity):
    question = activity_question(activity)
    data = request.POST if request.method == "POST" else None
    details = ActivitySettingsForm(data, instance=activity, prefix="activity")
    details_valid = details.is_valid() if data is not None else False
    initial_schema = schema_for_question(question)
    schema_form = SchemaEditorForm(
        data,
        initial={"schema": initial_schema},
        published=details.cleaned_data.get("is_active", False)
        if data is not None
        else False,
    )
    schema_valid = schema_form.is_valid() if data is not None else False
    if data is not None and details_valid and schema_valid:
        with transaction.atomic():
            current = Activity.objects.select_for_update().get(pk=activity.pk)
            if (
                current.deleted_at
                or current.revision != details.cleaned_data["revision"]
            ):
                details.add_error(
                    None,
                    "Someone saved changes while you were editing. Reload before saving again.",
                )
            else:
                # Plot preferences do not increment the activity revision. Refresh
                # after taking its lock so a schema edit retains newer chart settings.
                question.refresh_from_db(fields=["options_json"])
                schema = version_questions(
                    initial_schema, schema_form.cleaned_data["schema"]
                )
                # First edit of an original activity adapts its existing question schema.
                if schema != initial_schema:
                    question.is_active = False
                    question.save(update_fields=["is_active"])
                    options = {**question.options_json, SCHEMA_KEY: schema}
                    Question.objects.create(
                        activity=activity,
                        slug=question.slug,
                        version=question.version + 1,
                        question_text=details.cleaned_data["title"],
                        field_type="activity",
                        options_json=options,
                    )
                elif activity.activity_type != BUILDER:
                    # Adopting the shared builder without changing questions retains
                    # the active question and all existing cohort responses.
                    question.options_json = {
                        **question.options_json,
                        SCHEMA_KEY: schema,
                    }
                    question.field_type = "activity"
                    question.save(update_fields=["options_json", "field_type"])
                activity = details.save(commit=False)
                activity.activity_type = BUILDER
                activity.revision = current.revision + 1
                activity.save()
                messages.success(
                    request, "Activity saved." if activity.is_active else "Draft saved."
                )
                return redirect(request.path)
    if data is not None:
        try:
            initial_schema = json.loads(data.get("schema", ""))
            if not isinstance(initial_schema, dict) or not isinstance(
                initial_schema.get("sections"), list
            ):
                initial_schema = schema_for_question(question)
        except (ValueError, TypeError):
            initial_schema = schema_for_question(question)
    embed_url = request.build_absolute_uri(
        reverse("activity-iframe-entry", args=[activity.slug])
    )
    embed_code = '<iframe src="{}" title="{}" width="100%" height="800" style="border:0" loading="lazy"></iframe>'.format(
        escape(embed_url), escape(activity.title)
    )
    return render(
        request,
        "research/flexible_editor.html",
        {
            **activity_links(activity),
            "activity_settings": details,
            "schema_form": schema_form,
            "builder_schema": initial_schema,
            "field_types": [
                {"value": key, "label": label} for key, label in FIELD_TYPES
            ],
            "question": question,
            "embed_url": embed_url,
            "embed_code": embed_code,
        },
    )


def preview(request, activity):
    question = current_question(activity)
    return render(
        request,
        "course_iframe/flexible_form.html",
        {
            **activity_links(activity),
            "groups": section_formsets(question),
            "question": question,
            "preview": True,
        },
    )


def student(request, participant, activity):
    question = current_question(activity)
    if request.method == "POST":
        try:
            revision = int(request.POST.get("revision", ""))
        except (ValueError, TypeError):
            revision = None
        if revision != activity.revision or request.POST.get("question_id") != str(
            question.pk
        ):
            messages.error(
                request, "This activity has changed. Please complete the updated form."
            )
            return redirect("activity-student", activity_slug=activity.slug)
    groups = section_formsets(
        question, participant, request.POST if request.method == "POST" else None
    )
    error = None
    if request.method == "POST":
        valid = bool(groups)
        for group in groups:
            valid = group["formset"].is_valid() and valid
        if valid:
            try:
                save_entries(activity, participant, question, groups, revision)
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                messages.success(request, "Your answers have been saved.")
                return redirect("activity-results", activity_slug=activity.slug)
    return render(
        request,
        "course_iframe/flexible_form.html",
        {
            **activity_links(activity),
            "groups": groups,
            "question": question,
            "participant": participant,
            "submission_error": error,
        },
    )


def results(request, participant, activity):
    question = current_question(activity)
    responses = (
        Response.objects.filter(activity=activity, participant=participant)
        .select_related("question")
        .order_by("-created_at")
    )
    return render(
        request,
        "course_iframe/flexible_results.html",
        {
            **activity_links(activity),
            "participant": participant,
            "question": question,
            "own_entries": [
                {"response": response, "answers": response_answers(response)}
                for response in responses
            ],
            **plot_context(question, participant),
        },
    )


def _chart_has_answers(card):
    data = card["data"]
    if card["kind"] == "scatter":
        return bool(data["points"])
    if card["kind"] == "stacked":
        return any(row["total"] for row in data["rows"])
    if card["kind"] == "word_cloud":
        return bool(data.get("answer_count", data["words"]))
    return bool(data.get("total", data.get("count", 0)))


def plots(request, activity):
    question = current_question(activity)
    url = reverse("research-activity-plots", args=[activity.slug])
    source = request.POST if request.method == "POST" else request.GET
    participants = list(
        Participant.objects.filter(responses__activity=activity)
        .distinct()
        .order_by("participant_id")
    )
    highlight = next(
        (p for p in participants if p.participant_id == source.get("highlight", "")),
        None,
    )
    visibility_id = (
        request.POST.get("visibility_chart_id") if request.method == "POST" else None
    )
    if visibility_id:
        with transaction.atomic():
            current = Activity.objects.select_for_update().get(pk=activity.pk)
            if (
                current.deleted_at
                or request.POST.get("question_id") != str(question.pk)
                or not Question.objects.filter(pk=question.pk, is_active=True).exists()
            ):
                messages.error(
                    request, "The questions changed. Reload the charts before saving."
                )
                return redirect(url)
            question = Question.objects.get(pk=question.pk)
            cards = plot_context(question, researcher=True)["plot_cards"]
            if visibility_id not in {card["id"] for card in cards}:
                messages.error(request, "That chart is no longer available.")
                return redirect(url)
            selected = {card["id"] for card in cards if card["student_visible"]}
            if request.POST.get("student_visible") == "1":
                selected.add(visibility_id)
            else:
                selected.discard(visibility_id)
            question.options_json = {
                **question.options_json,
                "visualizations": default_plot_settings(question),
                "student_chart_ids": sorted(selected),
            }
            question.save(update_fields=["options_json"])
        return redirect(url + "#chart-" + visibility_id)

    form = PlotSettingsForm(
        request.POST if request.method == "POST" else None,
        question=question,
        initial=default_plot_settings(question),
    )
    preview_only = False
    if request.method == "POST" and form.is_valid():
        if "preview" in request.POST:
            question.options_json = {
                **question.options_json,
                "visualizations": form.cleaned_data,
            }
            preview_only = True
        else:
            with transaction.atomic():
                current = Activity.objects.select_for_update().get(pk=activity.pk)
                if (
                    current.deleted_at
                    or not Question.objects.filter(
                        pk=question.pk, is_active=True
                    ).exists()
                    or (
                        "question_id" in request.POST
                        and request.POST["question_id"] != str(question.pk)
                    )
                ):
                    messages.error(
                        request,
                        "The questions changed. Reload the plots before saving settings.",
                    )
                    return redirect(url)
                question = Question.objects.get(pk=question.pk)
                options = {**question.options_json}
                if "add_chart" in request.POST:
                    cards = plot_context(question, researcher=True)["plot_cards"]
                    candidate = copy(question)
                    candidate.options_json = {
                        **options,
                        "visualizations": form.cleaned_data,
                    }
                    proposed = plot_context(
                        candidate,
                        synthetic=True,
                        researcher=True,
                        include_library=False,
                    )["plot_cards"]
                    if {card["id"] for card in proposed}.issubset(
                        {card["id"] for card in cards}
                    ):
                        messages.info(
                            request, "These charts are already in your workspace."
                        )
                        return redirect(url)
                    library = list(options.get("chart_library", []))
                    if len(library) >= 50:
                        form.add_error(
                            None,
                            "This activity already has 50 custom chart configurations.",
                        )
                    else:
                        selected = [
                            card["id"] for card in cards if card["student_visible"]
                        ]
                        library.append(form.cleaned_data)
                        options.update(
                            visualizations=default_plot_settings(question),
                            chart_library=library,
                            student_chart_ids=selected,
                        )
                else:
                    # Compatibility for existing clients saving the original single configuration.
                    options["visualizations"] = form.cleaned_data
                if not form.errors:
                    question.options_json = options
                    question.save(update_fields=["options_json"])
            if not form.errors:
                messages.success(
                    request,
                    "Chart added. Check Show to students to share it."
                    if "add_chart" in request.POST
                    else "Student plots updated.",
                )
                return redirect(url)

    context = plot_context(
        question, highlight, researcher=True, include_library=not preview_only
    )
    has_synthetic = False
    using_synthetic = False
    if preview_only:
        empty_ids = {
            card["id"] for card in context["plot_cards"] if not _chart_has_answers(card)
        }
        if empty_ids:
            examples = plot_context(
                question, synthetic=True, researcher=True, include_library=False
            )
            example_cards = {card["id"]: card for card in examples["plot_cards"]}
            if len(empty_ids) == len(context["plot_cards"]):
                context = examples
                using_synthetic = True
                highlight = None
            for index, card in enumerate(context["plot_cards"]):
                if card["id"] in empty_ids and card["id"] in example_cards:
                    card = {**example_cards[card["id"]], "synthetic": True}
                    context["plot_cards"][index] = card
                    has_synthetic = True
    return render(
        request,
        "research/flexible_plots.html",
        {
            **activity_links(activity),
            **context,
            "plot_form": form,
            "is_research_view": True,
            "question": question,
            "preview_only": preview_only,
            "using_synthetic": using_synthetic,
            "has_synthetic": has_synthetic,
            "plot_fields": list(field_catalog(question).values()),
            "focus_choices": [p.participant_id for p in participants],
            "participant": highlight,
            "highlight_name": highlight.participant_id if highlight else "",
        },
    )

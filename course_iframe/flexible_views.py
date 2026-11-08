"""The common editor and student experience for all activity question combinations."""

import json
from copy import copy

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils import translation

from .localization import (
    LANGUAGES,
    UI_COPY,
    UI_EDITABLE,
    language_status,
    language_url,
    requested_language,
    missing_translations,
    semantic,
    translated_activity,
    ui_copy,
)

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
    encoded_answers,
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
                if semantic(schema) != semantic(initial_schema):
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
                elif schema != initial_schema or activity.activity_type != BUILDER:
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
                selected = (
                    request.POST.get("editor_language") or activity.source_language
                )
                if current.source_language != activity.source_language:
                    selected = activity.source_language
                return redirect(
                    language_url(
                        request.path,
                        selected
                        if selected in dict(LANGUAGES)
                        else activity.source_language,
                        activity.source_language,
                    )
                )
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
            "language_editor": {
                "source": activity.source_language,
                "selected": (
                    request.POST.get("editor_language") or request.GET.get("lang")
                )
                if (request.POST.get("editor_language") or request.GET.get("lang"))
                in dict(LANGUAGES)
                else activity.source_language,
                "translations": details.cleaned_data.get(
                    "activity_translations", activity.translations
                )
                if data is not None
                else activity.translations,
                "languages": [{"code": code, "name": name} for code, name in LANGUAGES],
                "ui": UI_COPY,
            },
            "ui_editable": [
                {"key": key, "label": UI_COPY["en"][key]} for key in UI_EDITABLE
            ],
            "language_versions": language_status(
                activity, schema_for_question(question)
            ),
        },
    )


def preview(request, activity):
    return student(request, None, activity, preview=True)


def _student_context(activity, language, preview=False, question=None):
    context = activity_links(activity, language)
    context.update(
        activity=translated_activity(activity, language),
        page_language=language,
        preview=preview,
        translation_incomplete=bool(
            preview
            and question
            and missing_translations(activity, schema_for_question(question), language)
        ),
    )
    if preview:
        context["activity_form_url"] = language_url(
            reverse("research-preview-activity", args=[activity.slug]),
            language,
            activity.source_language,
        )
        context["activity_results_url"] = language_url(
            reverse("research-preview-results", args=[activity.slug]),
            language,
            activity.source_language,
        )
    return context


def student(request, participant, activity, preview=False):
    question = current_question(activity)
    language = requested_language(
        request, activity, schema_for_question(question), preview=preview
    )
    with translation.override(language):
        return _student(request, participant, activity, question, language, preview)


def _student(request, participant, activity, question, language, preview):
    ui = ui_copy(activity, language)
    context = _student_context(activity, language, preview, question)
    if request.method == "POST":
        try:
            revision = int(request.POST.get("revision", ""))
        except (ValueError, TypeError):
            revision = None
        if revision != activity.revision or request.POST.get("question_id") != str(
            question.pk
        ):
            messages.error(request, ui["stale"])
            return redirect(context["activity_form_url"])
    groups = section_formsets(
        question,
        participant,
        request.POST if request.method == "POST" else None,
        language=language,
    )
    error = None
    if request.method == "POST":
        valid = bool(groups)
        for group in groups:
            valid = group["formset"].is_valid() and valid
        if valid:
            if preview:
                examples, entries = [], []
                display_title = translated_activity(activity, language).title
                for group in groups:
                    for form in group["formset"]:
                        if not form.cleaned_data:
                            continue
                        answers = encoded_answers(form)
                        response = Response(
                            question=question,
                            activity=activity,
                            question_version=question.version,
                            language=language,
                            payload={
                                "section_id": group["section"]["id"],
                                "answers": answers,
                            },
                            display_json={
                                "section": semantic(group["section"]),
                                "activity_title": display_title,
                            },
                        )
                        examples.append(response)
                        entries.append((group["section"]["id"], answers, True))
                return _render_results(
                    request,
                    None,
                    activity,
                    question,
                    language,
                    preview=True,
                    responses=examples,
                    preview_entries=entries,
                )
            try:
                save_entries(
                    activity, participant, question, groups, revision, language=language
                )
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                return redirect(context["activity_results_url"])
    return render(
        request,
        "course_iframe/flexible_form.html",
        {
            **context,
            "groups": groups,
            "question": question,
            "participant": participant,
            "submission_error": error,
        },
    )


def results(request, participant, activity, preview=False):
    question = current_question(activity)
    language = requested_language(
        request, activity, schema_for_question(question), preview=preview
    )
    with translation.override(language):
        return _render_results(
            request, participant, activity, question, language, preview=preview
        )


def _render_results(
    request,
    participant,
    activity,
    question,
    language,
    preview=False,
    responses=None,
    preview_entries=None,
):
    if responses is None:
        responses = (
            Response.objects.filter(activity=activity, participant=participant)
            .select_related("question")
            .order_by("-created_at")
            if participant
            else []
        )
    return render(
        request,
        "course_iframe/flexible_results.html",
        {
            **_student_context(activity, language, preview, question),
            "participant": participant,
            "question": question,
            "show_personal": bool(participant or preview),
            "own_entries": [
                {
                    "response": response,
                    "answers": response_answers(response, language=language),
                }
                for response in responses
            ],
            **plot_context(
                question,
                participant,
                language=language,
                preview_entries=preview_entries,
            ),
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
    language = requested_language(
        request, activity, schema_for_question(question), preview=True
    )
    url = language_url(
        reverse("research-activity-plots", args=[activity.slug]),
        language,
        activity.source_language,
    )
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
            cards = plot_context(question, researcher=True, language=language)[
                "plot_cards"
            ]
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
        language=language,
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
                    cards = plot_context(question, researcher=True, language=language)[
                        "plot_cards"
                    ]
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
                        language=language,
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
        question,
        highlight,
        researcher=True,
        include_library=not preview_only,
        language=language,
    )
    has_synthetic = False
    using_synthetic = False
    if preview_only:
        empty_ids = {
            card["id"] for card in context["plot_cards"] if not _chart_has_answers(card)
        }
        if empty_ids:
            examples = plot_context(
                question,
                synthetic=True,
                researcher=True,
                include_library=False,
                language=language,
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
            **activity_links(activity, language),
            **context,
            "plot_form": form,
            "is_research_view": True,
            "question": question,
            "preview_only": preview_only,
            "using_synthetic": using_synthetic,
            "has_synthetic": has_synthetic,
            "plot_fields": list(field_catalog(question, language).values()),
            "plot_languages": language_status(activity, schema_for_question(question)),
            "focus_choices": [p.participant_id for p in participants],
            "participant": highlight,
            "show_personal": bool(highlight),
            "highlight_name": highlight.participant_id if highlight else "",
        },
    )

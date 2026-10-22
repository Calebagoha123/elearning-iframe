import json

from django import forms
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .activity_builder import activity_question
from .flexible import schema_for_question
from .models import Activity, Response
from .services import (
    get_or_create_participant,
    save_housing_response,
    save_relationship_response,
)


def field(key, kind="text", **kwargs):
    return {
        "id": key,
        "label": key.replace("_", " ").title(),
        "type": kind,
        "required": True,
        "help_text": "",
        "choices": [],
        "allow_other": False,
        "min_value": "",
        **kwargs,
    }


def choices(*labels):
    return [
        {"value": str(index + 1), "label": label} for index, label in enumerate(labels)
    ]


def example_schema():
    return {
        "sections": [
            {
                "id": "once",
                "title": "About you",
                "instructions": "Answer these once.",
                "repeatable": False,
                "add_label": "Add another entry",
                "fields": [
                    field("comments", "long_text"),
                    field(
                        "preferences",
                        "multiple",
                        choices=choices("One", "Two", "Three"),
                    ),
                ],
            },
            {
                "id": "records",
                "title": "Entries",
                "instructions": "Add each item.",
                "repeatable": True,
                "add_label": "Add another item",
                "fields": [
                    field(
                        "category",
                        "choice",
                        choices=choices("Option A", "Option B"),
                        allow_other=True,
                    ),
                    field("rating", "likert", choices=choices("Low", "Middle", "High")),
                    field("cost", "decimal", min_value="0"),
                    field("size", "integer", min_value="0"),
                ],
            },
        ]
    }


@override_settings(RESEARCH_ADMIN_PASSWORD="research-pass", NOMINATIM_VERIFY=False)
class FlexibleActivityTests(TestCase):
    def setUp(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})

    def create(self, title="Course activity"):
        response = self.client.post(
            reverse("research-add-activity"),
            {"title": title, "description": "Course instructions"},
        )
        self.assertEqual(response.status_code, 302)
        activity = Activity.objects.get(title=title, activity_type="builder")
        self.assertRedirects(
            response, reverse("research-edit-activity", args=[activity.slug])
        )
        return activity

    def form_payload(self, form):
        result = {}
        for bound in form:
            value = bound.value()
            if isinstance(bound.field, forms.BooleanField):
                if value:
                    result[bound.html_name] = "on"
            elif value is not None:
                result[bound.html_name] = value
        return result

    def editor_payload(self, activity, schema=None, published=True):
        page = self.client.get(reverse("research-edit-activity", args=[activity.slug]))
        self.assertEqual(page.status_code, 200)
        data = self.form_payload(page.context["activity_settings"])
        data["schema"] = json.dumps(
            schema if schema is not None else page.context["builder_schema"]
        )
        if published:
            data["activity-is_active"] = "on"
        else:
            data.pop("activity-is_active", None)
        return data

    def publish(self, activity, schema=None):
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]),
            self.editor_payload(activity, schema),
        )
        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        self.assertTrue(activity.is_active)
        return activity_question(activity)

    def student(self, activity, name="student-a"):
        client = Client()
        self.assertEqual(
            client.post(
                reverse("activity-iframe-entry", args=[activity.slug]),
                {"pseudoname": name},
            ).status_code,
            302,
        )
        return client

    def answers(self, activity, row_count=2):
        activity.refresh_from_db()
        question = activity_question(activity)
        data = {
            "revision": activity.revision,
            "question_id": question.pk,
            "section-once-TOTAL_FORMS": 1,
            "section-once-INITIAL_FORMS": 0,
            "section-once-0-comments": "A free response",
            "section-once-0-preferences": ["1", "3"],
            "section-records-TOTAL_FORMS": row_count,
            "section-records-INITIAL_FORMS": 0,
        }
        for index in range(row_count):
            data.update(
                {
                    f"section-records-{index}-category": "1",
                    f"section-records-{index}-rating": "3",
                    f"section-records-{index}-cost": str(10 + index),
                    f"section-records-{index}-size": str(2 + index),
                }
            )
        return data

    def test_individual_question_versions_increment_only_for_changed_questions(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        old = activity_question(activity)
        schema = schema_for_question(old)
        schema["sections"][0]["fields"][0]["label"] = "More comments"
        self.publish(activity, schema)
        updated = schema_for_question(activity_question(activity))
        fields = {
            f["id"]: f for section in updated["sections"] for f in section["fields"]
        }
        self.assertEqual(fields["comments"]["version"], 2)
        self.assertEqual(fields["preferences"]["version"], 1)
        self.assertEqual(fields["rating"]["version"], 1)
        active_pk = activity_question(activity).pk
        fields["comments"]["version"] = 999
        self.publish(activity, updated)
        self.assertEqual(activity_question(activity).pk, active_pk)
        self.assertEqual(
            schema_for_question(activity_question(activity))["sections"][0]["fields"][
                0
            ]["version"],
            2,
        )
        removed = schema_for_question(activity_question(activity))
        removed["sections"][0]["fields"].pop(0)
        self.publish(activity, removed)
        self.assertEqual(old.responses.count(), 3)
        history = self.client.get(
            reverse("research-activity-responses", args=[activity.slug]),
            {"versions": "all"},
        )
        self.assertContains(history, "A free response")
        self.assertContains(history, "Question v1")

    def test_activity_navigation_is_shared_and_builder_has_location_and_trash_icons(
        self,
    ):
        activity = self.create()
        self.publish(activity, example_schema())
        editor = self.client.get(
            reverse("research-edit-activity", args=[activity.slug])
        )
        self.assertContains(editor, '"label": "Location"')
        self.assertNotContains(editor, '"label": "Verified place"')
        self.assertContains(
            editor, 'data-action="question-remove" aria-label="Delete question"'
        )
        self.assertContains(editor, "data-question-version")
        for page_route in [
            "research-edit-activity",
            "research-activity-responses",
            "research-activity-plots",
        ]:
            page = self.client.get(reverse(page_route, args=[activity.slug]))
            self.assertContains(
                page, '<nav class="activity-nav" aria-label="Activity pages">'
            )
            for link_route in [
                "research-edit-activity",
                "research-activity-responses",
                "research-activity-plots",
            ]:
                self.assertContains(page, reverse(link_route, args=[activity.slug]))
            self.assertContains(page, 'aria-current="page"')
            self.assertContains(page, 'class="external-link__arrow"')
            self.assertNotContains(
                page,
                'class="button button-secondary" href="'
                + reverse("research-activity-plots", args=[activity.slug]),
            )

    def test_original_activities_use_the_same_plot_workspace_without_conversion(self):
        for slug in ["housing", "relationship"]:
            activity = Activity.objects.get(slug=slug)
            before = activity.activity_type
            page = self.client.get(reverse("research-activity-plots", args=[slug]))
            self.assertTemplateUsed(page, "research/flexible_plots.html")
            self.assertContains(page, "data-plot-builder")
            self.assertContains(page, 'name="chart_type"')
            self.assertContains(page, 'name="preview"')
            self.assertContains(page, "Add chart")
            activity.refresh_from_db()
            self.assertEqual(activity.activity_type, before)

    def test_plot_preview_is_private_then_save_applies_selected_question_to_students(
        self,
    ):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        question = activity_question(activity)
        url = reverse("research-activity-plots", args=[activity.slug])
        config = {
            "section": "records",
            "chart_type": "distribution",
            "question": "rating",
        }
        preview = self.client.post(url, {**config, "preview": "1"})
        self.assertContains(preview, "Preview only")
        self.assertEqual(
            [chart["field_id"] for chart in preview.context["distributions"]],
            ["rating"],
        )
        self.assertFalse(preview.context["scatter_enabled"])
        self.assertFalse(preview.context["word_clouds"])
        question.refresh_from_db()
        self.assertNotIn("visualizations", question.options_json)
        self.assertEqual(
            self.client.get(url).context["plot_settings"]["chart_type"], "auto"
        )
        self.client.post(url, {**config, "save": "1"})
        self.assertEqual(activity_question(activity).pk, question.pk)
        self.assertEqual(activity.responses.count(), 3)
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertEqual(
            [chart["field_id"] for chart in results.context["distributions"]],
            ["rating"],
        )
        self.assertFalse(results.context["scatter_enabled"])

    def test_saving_plots_for_originals_updates_student_results_without_versioning(
        self,
    ):
        participant = get_or_create_participant("student-a")
        for slug, field_id in [
            ("housing", "monthly_rent"),
            ("relationship", "income_comparison_value"),
        ]:
            activity = Activity.objects.get(slug=slug)
            old = activity_question(activity)
            if slug == "housing":
                save_housing_response(
                    participant,
                    old,
                    {"district": "Berlin", "monthly_rent": 850, "rooms": 2},
                )
            else:
                save_relationship_response(participant, old, "friend", "Friend")
            url = reverse("research-activity-plots", args=[slug])
            self.assertEqual(
                self.client.post(
                    url, {"chart_type": "distribution", "question": field_id}
                ).status_code,
                302,
            )
            self.assertEqual(activity_question(activity).pk, old.pk)
            student = self.student(activity)
            page = student.get(reverse("activity-results", args=[slug]))
            self.assertTemplateUsed(page, "course_iframe/flexible_results.html")
            self.assertFalse(page.context["scatter_enabled"])
            self.assertEqual(
                len(
                    page.context["numeric_summaries"]
                    if slug == "housing"
                    else page.context["distributions"]
                ),
                1,
            )

    def test_plot_eligibility_and_highlight_do_not_persist_a_personal_preview(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        other = self.student(activity, "student-b")
        other.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity, 1)
        )
        url = reverse("research-activity-plots", args=[activity.slug])
        invalid = self.client.post(
            url, {"chart_type": "scatter", "section": "once", "x": "cost", "y": "size"}
        )
        self.assertTrue(invalid.context["plot_form"].errors)
        identical = self.client.post(
            url, {"chart_type": "scatter", "x": "cost", "y": "cost"}
        )
        self.assertContains(
            identical, "Choose two different numeric questions or ratios"
        )
        page = self.client.get(url, {"highlight": "student-a"})
        self.assertEqual(
            sum(point["is_yours"] for point in page.context["scatter_points"]), 2
        )
        self.assertEqual(len(page.context["scatter_points"]), 3)
        self.assertNotIn("visualizations", activity_question(activity).options_json)

    def test_new_activity_has_automatic_unique_stable_link_and_no_format_chooser(self):
        page = self.client.get(reverse("research-add-activity"))
        self.assertNotContains(page, "Link name")
        self.assertNotContains(page, "Choose an activity format")
        self.assertEqual(set(page.context["form"].fields), {"title", "description"})
        activity = self.create()
        first_slug = activity.slug
        self.assertTrue(first_slug.startswith("course-activity-"))
        self.assertFalse(activity.is_active)
        second = self.client.post(
            reverse("research-add-activity"), {"title": activity.title}
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Activity.objects.filter(title=activity.title).count(), 2)
        self.assertEqual(
            Activity.objects.filter(title=activity.title)
            .values("slug")
            .distinct()
            .count(),
            2,
        )
        data = self.editor_payload(activity, published=False)
        data["activity-title"] = "Renamed"
        self.client.post(reverse("research-edit-activity", args=[activity.slug]), data)
        activity.refresh_from_db()
        self.assertEqual(activity.slug, first_slug)

    def test_existing_activities_open_as_repeatable_question_groups_in_shared_editor(
        self,
    ):
        for slug in ["housing", "relationship"]:
            page = self.client.get(reverse("research-edit-activity", args=[slug]))
            self.assertTemplateUsed(page, "research/flexible_editor.html")
            section = page.context["builder_schema"]["sections"][0]
            self.assertTrue(section["repeatable"])
            kinds = [spec["type"] for spec in section["fields"]]
            if slug == "housing":
                self.assertIn("place", kinds)
                self.assertIn("currency_eur", kinds)
                self.assertIn("decimal", kinds)
            else:
                self.assertEqual(kinds, ["choice", "likert"])

    def test_adopting_originals_preserves_active_question_and_existing_cohort(self):
        participant = get_or_create_participant("existing-student")
        for slug in ["housing", "relationship"]:
            activity = Activity.objects.get(slug=slug)
            old = activity_question(activity)
            if slug == "housing":
                save_housing_response(
                    participant,
                    old,
                    {"district": "Berlin", "monthly_rent": 850, "rooms": 2},
                )
            else:
                save_relationship_response(participant, old, "friend", "Friend")
            self.publish(activity)
            activity.refresh_from_db()
            self.assertEqual(activity.activity_type, "builder")
            self.assertEqual(activity_question(activity).pk, old.pk)
            self.assertEqual(activity.questions.count(), 1)
            plots = self.client.get(reverse("research-activity-plots", args=[slug]))
            self.assertEqual(plots.context["response_count"], 1)
            self.assertEqual(
                self.client.get(reverse("research-activity-responses", args=[slug]))
                .context["page"]
                .paginator.count,
                1,
            )
            student = self.student(activity)
            self.assertTemplateUsed(
                student.get(reverse("activity-student", args=[slug])),
                "course_iframe/flexible_form.html",
            )

    def test_any_mix_of_questions_with_single_and_repeatable_sections(self):
        activity = self.create()
        question = self.publish(activity, example_schema())
        student = self.student(activity)
        page = student.get(reverse("activity-student", args=[activity.slug]))
        self.assertEqual(len(page.context["groups"]), 2)
        self.assertFalse(page.context["groups"][0]["section"]["repeatable"])
        self.assertTrue(page.context["groups"][1]["section"]["repeatable"])
        self.assertContains(page, "Add another item")
        self.assertContains(page, "Course instructions")
        response = student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        self.assertRedirects(
            response, reverse("activity-results", args=[activity.slug])
        )
        self.assertEqual(Response.objects.filter(question=question).count(), 3)
        repeated = list(question.responses.filter(payload__section_id="records"))
        self.assertEqual(repeated[0].payload["answers"]["cost"], "11")
        once = question.responses.get(payload__section_id="once")
        self.assertEqual(once.payload["answers"]["preferences"], ["1", "3"])
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertContains(results, "Option A")
        self.assertContains(results, "High")
        self.assertContains(results, "One, Three")
        self.assertContains(
            self.client.get(
                reverse("research-activity-responses", args=[activity.slug])
            ),
            "A free response",
        )
        self.assertContains(student.get(reverse("activity-landing")), activity.title)
        self.assertContains(
            student.get(reverse("student-summary")),
            reverse("activity-results", args=[activity.slug]),
        )

    def test_typed_answers_reject_forged_choices_scales_numbers_and_missing_values(
        self,
    ):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        url = reverse("activity-student", args=[activity.slug])
        for overrides in [
            {"section-records-0-category": "forged"},
            {"section-records-0-rating": "9"},
            {"section-records-0-cost": "NaN"},
            {"section-records-0-cost": "-1"},
            {"section-records-0-size": "2.5"},
            {"section-once-0-comments": ""},
            {"section-once-0-preferences": ["forged"]},
        ]:
            with self.subTest(overrides=overrides):
                self.assertEqual(
                    student.post(
                        url, {**self.answers(activity), **overrides}
                    ).status_code,
                    200,
                )
                self.assertFalse(activity.responses.exists())

    def test_other_choice_requires_text_and_is_reviewed_and_plotted(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        data = self.answers(activity, 1)
        data["section-records-0-category"] = "__other__"
        url = reverse("activity-student", args=[activity.slug])
        self.assertContains(student.post(url, data), "Describe your other answer")
        data["section-records-0-category__other"] = "My category"
        self.assertEqual(student.post(url, data).status_code, 302)
        self.assertContains(
            self.client.get(
                reverse("research-activity-responses", args=[activity.slug])
            ),
            "My category",
        )
        self.assertContains(
            student.get(reverse("activity-results", args=[activity.slug])),
            "My category",
        )

    def test_multiple_entries_can_be_updated_and_removed_without_duplication(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        url = reverse("activity-student", args=[activity.slug])
        student.post(url, self.answers(activity))
        page = student.get(url)
        data = {
            "revision": activity.revision,
            "question_id": activity_question(activity).pk,
        }
        first_pk = None
        for group in page.context["groups"]:
            formset = group["formset"]
            data.update(self.form_payload(formset.management_form))
            for form in formset:
                data.update(self.form_payload(form))
            if group["section"]["id"] == "records":
                first_pk = formset.forms[0]["response_id"].value()
                data[formset.prefix + "-TOTAL_FORMS"] = 1
                data[formset.prefix + "-0-cost"] = "99"
        self.assertEqual(student.post(url, data).status_code, 302)
        self.assertEqual(activity.responses.count(), 2)
        self.assertEqual(
            Response.objects.get(pk=first_pk).payload["answers"]["cost"], "99"
        )

    def test_cross_participant_entry_ids_and_duplicate_ids_cannot_modify_responses(
        self,
    ):
        activity = self.create()
        self.publish(activity, example_schema())
        owner = self.student(activity)
        owner.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        entry = activity.responses.filter(payload__section_id="records").first()
        other = self.student(activity, "student-b")
        data = self.answers(activity)
        data["section-records-0-response_id"] = entry.pk
        response = other.post(reverse("activity-student", args=[activity.slug]), data)
        self.assertContains(response, "does not belong to you")
        self.assertEqual(activity.responses.count(), 3)
        data["section-records-1-response_id"] = entry.pk
        self.assertContains(
            other.post(reverse("activity-student", args=[activity.slug]), data),
            "same entry was submitted twice",
        )

    def test_independent_plots_support_numeric_scatter_likert_and_choice_counts(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        response = self.client.get(
            reverse("research-activity-plots", args=[activity.slug])
        )
        self.assertEqual(response.context["response_count"], 3)
        self.assertEqual(response.context["participant_count"], 1)
        self.assertEqual(len(response.context["scatter_points"]), 2)
        self.assertEqual(response.context["stacked"]["title"], "Rating by category")
        self.assertTrue(response.context["distributions"])
        self.assertTrue(response.context["numeric_summaries"])
        self.assertTrue(response.context["word_clouds"])
        for point in response.context["scatter_points"]:
            self.assertEqual(set(point), {"x", "y", "is_yours"})
        source = self.client.get(reverse("research-housing-plots"))
        self.assertFalse(source.context["scatter_points"])
        url = reverse("research-activity-plots", args=[activity.slug])
        old_pk = activity_question(activity).pk
        self.assertRedirects(
            self.client.post(
                url,
                {
                    "x": "size",
                    "y": "cost",
                    "y_denominator": "size",
                    "category": "category",
                    "scale": "rating",
                },
            ),
            url,
        )
        self.assertEqual(activity_question(activity).pk, old_pk)
        self.assertEqual(activity.responses.count(), 3)
        self.assertContains(self.client.get(url), "Cost ÷ Size")

    def test_plot_pairs_must_share_a_section_and_changed_field_types_do_not_break_plots(
        self,
    ):
        activity = self.create()
        schema = example_schema()
        schema["sections"][0]["fields"].append(field("age", "integer"))
        self.publish(activity, schema)
        url = reverse("research-activity-plots", args=[activity.slug])
        invalid = self.client.post(url, {"x": "age", "y": "cost"})
        self.assertContains(invalid, "must come from the same section")
        self.client.post(
            url, {"x": "size", "y": "cost", "category": "category", "scale": "rating"}
        )
        changed = schema_for_question(activity_question(activity))
        changed["sections"][1]["fields"][2]["type"] = "text"
        self.publish(activity, changed)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["scatter_enabled"])

    def test_scatter_ratios_skip_zero_denominators_and_csv_labels_are_readable(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        data = self.answers(activity)
        data["section-records-0-size"] = 0
        student.post(reverse("activity-student", args=[activity.slug]), data)
        url = reverse("research-activity-plots", args=[activity.slug])
        self.client.post(url, {"x": "size", "y": "cost", "y_denominator": "size"})
        page = self.client.get(url)
        self.assertEqual(len(page.context["scatter_points"]), 1)
        csv = self.client.get(
            reverse("research-export-csv"), {"activity": activity.slug}
        )
        self.assertContains(csv, "Comments [comments]")
        self.assertContains(csv, "A free response")
        self.assertContains(csv, "One, Three")

    def test_question_versions_preserve_historical_wording_and_answers(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        old = activity_question(activity)
        schema = schema_for_question(old)
        schema["sections"][0]["fields"][0]["label"] = "Updated wording"
        self.publish(activity, schema)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertEqual(activity_question(activity).version, old.version + 1)
        review = reverse("research-activity-responses", args=[activity.slug])
        self.assertEqual(self.client.get(review).context["page"].paginator.count, 0)
        history = self.client.get(review, {"versions": "all"})
        self.assertContains(history, "Comments")
        self.assertContains(history, "A free response")
        self.assertContains(history, "Earlier version")
        self.assertContains(
            student.get(reverse("activity-results", args=[activity.slug])),
            "A free response",
        )
        self.assertContains(
            self.client.get(
                reverse("research-export-csv"), {"activity": activity.slug}
            ),
            "A free response",
        )

    def test_noop_schema_does_not_create_a_version_and_link_survives_rename(self):
        activity = self.create()
        self.publish(activity, example_schema())
        old = activity_question(activity)
        data = self.editor_payload(activity)
        data["activity-title"] = "Renamed activity"
        self.client.post(reverse("research-edit-activity", args=[activity.slug]), data)
        self.assertEqual(activity_question(activity).pk, old.pk)
        self.assertEqual(activity.questions.count(), 2)
        self.assertTrue(
            Activity.objects.filter(
                slug=activity.slug, title="Renamed activity"
            ).exists()
        )

    def test_stale_editor_and_student_submissions_do_not_overwrite(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student_data = self.answers(activity)
        data = self.editor_payload(activity)
        url = reverse("research-edit-activity", args=[activity.slug])
        self.client.post(url, data)
        self.assertContains(
            self.client.post(url, {**data, "activity-title": "Stale"}),
            "Someone saved changes",
        )
        response = student.post(
            reverse("activity-student", args=[activity.slug]), student_data, follow=True
        )
        self.assertContains(response, "This activity has changed")
        self.assertFalse(activity.responses.exists())

    def test_schema_validation_and_empty_publishing(self):
        activity = self.create()
        url = reverse("research-edit-activity", args=[activity.slug])
        self.assertContains(
            self.client.post(url, self.editor_payload(activity, {"sections": []})),
            "Add at least one question",
        )
        for change in [
            "missing_label",
            "duplicate_ids",
            "bad_type",
            "duplicate_options",
            "short_scale",
        ]:
            schema = example_schema()
            if change == "missing_label":
                schema["sections"][0]["fields"][0]["label"] = ""
            if change == "duplicate_ids":
                schema["sections"][1]["fields"][0]["id"] = "comments"
            if change == "bad_type":
                schema["sections"][0]["fields"][0]["type"] = "forged"
            if change == "duplicate_options":
                schema["sections"][1]["fields"][0]["choices"] = choices("A", "A")
            if change == "short_scale":
                schema["sections"][1]["fields"][1]["choices"] = choices("A", "B")
            with self.subTest(change=change):
                response = self.client.post(url, self.editor_payload(activity, schema))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["schema_form"].errors)
                activity.refresh_from_db()
                self.assertFalse(activity.is_active)

    def test_single_sections_reject_multiple_entries_and_missing_management_forms(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        url = reverse("activity-student", args=[activity.slug])
        data = self.answers(activity)
        data["section-once-TOTAL_FORMS"] = 2
        self.assertEqual(student.post(url, data).status_code, 200)
        data = self.answers(activity)
        data.pop("section-records-TOTAL_FORMS")
        self.assertEqual(student.post(url, data).status_code, 200)
        self.assertFalse(activity.responses.exists())

    def test_verified_place_and_currency_can_mix_with_likert(self):
        activity = self.create()
        schema = {
            "sections": [
                {
                    "id": "places",
                    "title": "Places",
                    "instructions": "",
                    "repeatable": True,
                    "add_label": "Add place",
                    "fields": [
                        field("place", "place"),
                        field("rent", "currency_eur", min_value="0"),
                        field(
                            "rating", "likert", choices=choices("Low", "Middle", "High")
                        ),
                    ],
                }
            ]
        }
        self.publish(activity, schema)
        student = self.student(activity)
        data = {
            "revision": activity.revision,
            "question_id": activity_question(activity).pk,
            "section-places-TOTAL_FORMS": 1,
            "section-places-INITIAL_FORMS": 0,
            "section-places-0-place": "Berlin",
            "section-places-0-rent": "850.50",
            "section-places-0-rating": "2",
        }
        url = reverse("activity-student", args=[activity.slug])
        self.assertContains(student.post(url, data), "verified place")
        data.update(
            {
                "section-places-0-place__osm_type": "relation",
                "section-places-0-place__osm_id": "62422",
            }
        )
        self.assertEqual(student.post(url, data).status_code, 302)
        payload = activity.responses.get().payload["answers"]
        self.assertEqual(payload["place"]["display_name"], "Berlin")
        self.assertEqual(payload["rent"], "850.50")

    def test_preview_is_read_only_and_opens_in_new_tab_everywhere(self):
        activity = self.create()
        self.publish(activity, example_schema())
        url = reverse("research-preview-activity", args=[activity.slug])
        response = self.client.get(url)
        self.assertContains(response, "data-preview")
        self.assertEqual(self.client.post(url, {}).status_code, 405)
        self.assertFalse(activity.responses.exists())
        for route in [
            "research-edit-activity",
            "research-activity-responses",
            "research-activity-plots",
        ]:
            page = self.client.get(reverse(route, args=[activity.slug]))
            self.assertContains(page, f'href="{url}" target="_blank" rel="noopener"')
        self.assertContains(
            self.client.get(reverse("research-dashboard")),
            f'href="{url}" target="_blank" rel="noopener"',
        )

    def test_drafts_are_grey_without_publication_badges_and_inaccessible_to_students(
        self,
    ):
        activity = self.create()
        page = self.client.get(reverse("research-dashboard"))
        self.assertContains(page, "activity-card--draft")
        self.assertNotContains(page, ">Published<")
        self.assertNotContains(page, ">Draft / closed<")
        self.assertEqual(
            self.client.get(
                reverse("activity-iframe-entry", args=[activity.slug])
            ).status_code,
            404,
        )
        self.publish(activity, example_schema())
        student = self.student(activity)
        data = self.editor_payload(activity, published=False)
        self.client.post(reverse("research-edit-activity", args=[activity.slug]), data)
        self.assertEqual(
            student.get(reverse("activity-student", args=[activity.slug])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("research-activity-plots", args=[activity.slug])
            ).status_code,
            200,
        )

    def test_research_routes_are_protected(self):
        activity = self.create()
        outsider = Client()
        for route in [
            "research-edit-activity",
            "research-activity-responses",
            "research-activity-plots",
            "research-preview-activity",
        ]:
            self.assertEqual(
                outsider.get(reverse(route, args=[activity.slug])).status_code, 302
            )
        self.assertEqual(
            outsider.post(
                reverse("research-add-activity"), {"title": "Unauthorized"}
            ).status_code,
            302,
        )

    @override_settings(
        CSP_FRAME_ANCESTORS=["https://old-moodle.example"],
        REQUIRE_CSP_FRAME_ANCESTORS=True,
    )
    def test_embedding_has_no_domain_restrictions(self):
        activity = self.create()
        self.publish(activity, example_schema())
        page = self.client.get(reverse("activity-iframe-entry", args=[activity.slug]))
        self.assertNotIn("X-Frame-Options", page)
        self.assertNotIn("Content-Security-Policy", page)

    def test_dashboard_cards_open_responses_and_delete_without_undo(self):
        activity = self.create("Removable activity")
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        response_ids = list(activity.responses.values_list("pk", flat=True))
        dashboard = self.client.get(reverse("research-dashboard"))
        self.assertContains(dashboard, 'aria-label="Edit Removable activity"')
        self.assertContains(dashboard, 'aria-label="Delete Removable activity"')
        self.assertNotContains(dashboard, ">Edit activity</a>")
        self.assertContains(
            dashboard,
            f'class="activity-card__link" href="{reverse("research-activity-responses", args=[activity.slug])}"',
        )
        self.assertNotContains(dashboard, ">View plots</a>")
        self.assertNotContains(dashboard, ">Responses</a>")
        delete_url = reverse("research-delete-activity", args=[activity.slug])
        self.assertEqual(Client().post(delete_url).status_code, 302)
        self.assertEqual(self.client.get(delete_url).status_code, 405)
        self.assertIsNone(Activity.objects.get(pk=activity.pk).deleted_at)
        page = self.client.post(delete_url, follow=True)
        self.assertNotContains(page, "Undo delete")
        self.assertNotContains(page, "/restore/")
        self.assertNotIn(
            activity.pk,
            [card["activity"].pk for card in page.context["activity_cards"]],
        )
        self.assertEqual(
            list(activity.responses.values_list("pk", flat=True)), response_ids
        )
        self.assertEqual(
            self.client.get(
                reverse("research-edit-activity", args=[activity.slug])
            ).status_code,
            404,
        )
        self.assertEqual(
            student.get(reverse("activity-student", args=[activity.slug])).status_code,
            404,
        )
        self.assertEqual(
            student.get(reverse("activity-results", args=[activity.slug])).status_code,
            404,
        )
        self.assertEqual(
            student.get(
                reverse("activity-iframe-entry", args=[activity.slug])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("research-export-csv"), {"activity": activity.slug}
            ).status_code,
            400,
        )
        self.assertNotIn(
            activity.slug,
            self.client.get(reverse("research-export-csv")).content.decode(),
        )
        self.assertEqual(
            self.client.post(
                f"/research/activities/{activity.slug}/restore/"
            ).status_code,
            404,
        )

    def test_synthetic_preview_without_responses_is_private_and_never_saved(self):
        from .flexible_plots import plot_context

        activity = self.create()
        question = self.publish(activity, example_schema())
        url = reverse("research-activity-plots", args=[activity.slug])
        with self.assertNumQueries(0):
            examples = plot_context(question, synthetic=True)
            self.assertEqual(examples, plot_context(question, synthetic=True))
        self.assertTrue(examples["scatter_points"])
        self.assertTrue(examples["stacked"]["legend"])
        self.assertTrue(examples["word_clouds"][0]["words"])
        preview = self.client.post(url, {"chart_type": "auto", "preview": "1"})
        self.assertContains(preview, "synthetic examples")
        self.assertContains(preview, "example participants")
        self.assertEqual(preview.context["participant_count"], 12)
        self.assertGreater(preview.context["response_count"], 12)
        self.assertFalse(Response.objects.filter(activity=activity).exists())
        self.assertNotIn("visualizations", activity_question(activity).options_json)
        real = self.client.post(
            url, {"chart_type": "auto", "preview": "1", "preview_data": "collected"}
        )
        self.assertGreater(real.context["response_count"], 0)
        self.assertTrue(real.context["using_synthetic"])
        saved = self.client.post(
            url,
            {"chart_type": "auto", "save": "1", "preview_data": "synthetic"},
            follow=True,
        )
        self.assertFalse(saved.context["using_synthetic"])
        self.assertEqual(saved.context["response_count"], 0)
        self.assertNotIn(
            "preview_data", activity_question(activity).options_json["visualizations"]
        )
        self.assertFalse(Response.objects.filter(activity=activity).exists())
        student = self.student(activity)
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertEqual(results.context["response_count"], 0)
        self.assertEqual(activity_question(activity).pk, question.pk)

    def test_chart_visibility_controls_student_results_without_removing_research_charts(
        self,
    ):
        activity = self.create()
        question = self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        url = reverse("research-activity-plots", args=[activity.slug])
        cards = self.client.get(url).context["plot_cards"]
        self.assertGreater(len(cards), 3)
        for card in cards:
            self.assertEqual(
                self.client.post(
                    url,
                    {
                        "question_id": question.pk,
                        "visibility_chart_id": card["id"],
                    },
                ).status_code,
                302,
            )
        researcher = self.client.get(url)
        self.assertEqual(len(researcher.context["plot_cards"]), len(cards))
        self.assertTrue(
            all(
                not card["student_visible"] for card in researcher.context["plot_cards"]
            )
        )
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertEqual(results.context["plot_cards"], [])
        self.assertNotContains(results, "data-flexible-scatter")
        self.assertContains(results, "No charts have been shared")
        self.client.post(
            url,
            {
                "question_id": question.pk,
                "visibility_chart_id": cards[0]["id"],
                "student_visible": "1",
            },
        )
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertEqual(
            [card["id"] for card in results.context["plot_cards"]], [cards[0]["id"]]
        )
        self.assertNotContains(results, "data-chart-visibility")
        self.assertEqual(activity_question(activity).pk, question.pk)
        self.assertEqual(activity.responses.count(), 3)

    def test_custom_charts_are_retained_and_shared_only_when_checked(self):
        activity = self.create()
        question = self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        url = reverse("research-activity-plots", args=[activity.slug])
        before = {card["id"] for card in self.client.get(url).context["plot_cards"]}
        self.client.post(
            url,
            {
                "question_id": question.pk,
                "add_chart": "1",
                "chart_type": "scatter",
                "x": "size",
                "y": "cost",
                "y_denominator": "size",
            },
        )
        page = self.client.get(url)
        added = [
            card for card in page.context["plot_cards"] if card["id"] not in before
        ]
        self.assertEqual(len(added), 1)
        self.assertFalse(added[0]["student_visible"])
        self.assertTrue(added[0]["data"]["points"])
        student_ids = {
            card["id"]
            for card in student.get(
                reverse("activity-results", args=[activity.slug])
            ).context["plot_cards"]
        }
        self.assertNotIn(added[0]["id"], student_ids)
        self.client.post(
            url,
            {
                "question_id": question.pk,
                "visibility_chart_id": added[0]["id"],
                "student_visible": "1",
            },
        )
        results = student.get(reverse("activity-results", args=[activity.slug]))
        self.assertIn(
            added[0]["id"], {card["id"] for card in results.context["plot_cards"]}
        )
        self.assertEqual(
            len(
                [
                    card
                    for card in results.context["plot_cards"]
                    if card["kind"] == "scatter"
                ]
            ),
            2,
        )
        self.assertEqual(
            len({card["data_id"] for card in results.context["plot_cards"]}),
            len(results.context["plot_cards"]),
        )
        self.assertEqual(activity_question(activity).pk, question.pk)
        schema = schema_for_question(question)
        schema["sections"][1]["fields"] = [
            f for f in schema["sections"][1]["fields"] if f["id"] != "cost"
        ]
        self.publish(activity, schema)
        fresh = activity_question(activity)
        self.assertNotIn(
            added[0]["id"],
            {card["id"] for card in self.client.get(url).context["plot_cards"]},
        )
        stale = self.client.post(
            url,
            {
                "question_id": question.pk,
                "visibility_chart_id": added[0]["id"],
                "student_visible": "1",
            },
            follow=True,
        )
        self.assertContains(stale, "The questions changed")
        unchanged = activity_question(activity)
        self.assertEqual(unchanged.options_json, fresh.options_json)

    def test_preview_uses_real_answers_and_examples_only_for_new_questions(self):
        activity = self.create()
        old = self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        schema = schema_for_question(old)
        schema["sections"][0]["fields"].append(
            field("new_question", "long_text", required=False)
        )
        self.publish(activity, schema)
        url = reverse("research-activity-plots", args=[activity.slug])
        preview = self.client.post(url, {"chart_type": "auto", "preview": "1"})
        cards = preview.context["plot_cards"]
        by_title = {card["title"]: card for card in cards}
        self.assertTrue(by_title["New Question"]["synthetic"])
        self.assertFalse(by_title["Comments"].get("synthetic", False))
        self.assertEqual(by_title["Rating"]["data"]["total"], 2)
        self.assertFalse(by_title["Rating"].get("synthetic", False))
        self.assertFalse(preview.context["using_synthetic"])
        self.assertEqual(preview.context["response_count"], 3)
        self.assertContains(preview, "Back to saved charts")
        self.assertNotContains(preview, 'name="preview_data"')
        self.assertNotIn("visualizations", activity_question(activity).options_json)
        schema["sections"][1]["fields"][1]["label"] = "Updated rating"
        self.publish(activity, schema)
        changed = self.client.post(url, {"chart_type": "auto", "preview": "1"})
        updated = next(
            card
            for card in changed.context["plot_cards"]
            if card["title"] == "Updated rating"
        )
        self.assertTrue(updated["synthetic"])
        self.assertEqual(activity.responses.count(), 3)

    def test_answered_charts_ignore_synthetic_requests(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        student.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        url = reverse("research-activity-plots", args=[activity.slug])
        page = self.client.post(
            url, {"chart_type": "auto", "preview": "1", "preview_data": "synthetic"}
        )
        self.assertFalse(page.context["has_synthetic"])
        self.assertFalse(page.context["using_synthetic"])
        self.assertEqual(page.context["response_count"], 3)
        self.assertTrue(
            all(not card.get("synthetic", False) for card in page.context["plot_cards"])
        )
        self.assertNotContains(page, 'name="preview_data"')

    def test_seed_command_preserves_builder_configuration_and_publication(self):
        from django.core.management import call_command

        activity = Activity.objects.get(slug="housing")
        self.publish(activity)
        data = self.editor_payload(activity, published=False)
        data["activity-title"] = "Updated housing course"
        self.client.post(reverse("research-edit-activity", args=[activity.slug]), data)
        before = activity_question(activity)
        call_command("seed_mvp", verbosity=0)
        activity.refresh_from_db()
        self.assertEqual(activity.activity_type, "builder")
        self.assertEqual(activity.title, "Updated housing course")
        self.assertFalse(activity.is_active)
        self.assertEqual(activity_question(activity).pk, before.pk)

    def test_editor_preserves_plot_changes_committed_while_form_is_validating(self):
        from unittest.mock import patch
        from .flexible import SchemaEditorForm

        activity = self.create()
        question = self.publish(activity, example_schema())
        schema = schema_for_question(question)
        schema["sections"][0]["fields"][0]["label"] = "New question wording"
        data = self.editor_payload(activity, schema)
        original = SchemaEditorForm.is_valid
        options = {
            **question.options_json,
            "student_chart_ids": [],
            "chart_library": [{"chart_type": "scatter", "x": "cost", "y": "size"}],
        }

        def validate_and_save_charts(form):
            valid = original(form)
            type(question).objects.filter(pk=question.pk).update(options_json=options)
            return valid

        with patch.object(SchemaEditorForm, "is_valid", validate_and_save_charts):
            response = self.client.post(
                reverse("research-edit-activity", args=[activity.slug]), data
            )
        self.assertEqual(response.status_code, 302)
        current = activity_question(activity)
        self.assertEqual(current.options_json.get("student_chart_ids"), [])
        self.assertEqual(
            current.options_json.get("chart_library"), options["chart_library"]
        )

    def test_student_can_remove_the_final_repeatable_entry(self):
        activity = self.create()
        self.publish(activity, example_schema())
        student = self.student(activity)
        url = reverse("activity-student", args=[activity.slug])
        student.post(url, self.answers(activity, 1))
        page = student.get(url)
        data = {
            "revision": activity.revision,
            "question_id": activity_question(activity).pk,
        }
        for group in page.context["groups"]:
            data.update(self.form_payload(group["formset"].management_form))
            for form in group["formset"]:
                data.update(self.form_payload(form))
        data["section-records-TOTAL_FORMS"] = 0
        self.assertEqual(student.post(url, data).status_code, 302)
        self.assertEqual(activity.responses.count(), 1)
        self.assertFalse(
            activity.responses.filter(payload__section_id="records").exists()
        )
        # A partially completed entry must still validate its required questions.
        data["section-records-TOTAL_FORMS"] = 1
        data["section-records-INITIAL_FORMS"] = 0
        data["section-records-0-response_id"] = ""
        data["section-records-0-rating"] = ""
        self.assertEqual(student.post(url, data).status_code, 200)
        self.assertEqual(activity.responses.count(), 1)

    def test_optional_only_single_section_can_be_skipped(self):
        activity = self.create()
        schema = example_schema()
        schema["sections"] = [schema["sections"][0]]
        schema["sections"][0]["fields"] = [
            field("comments", "long_text", required=False)
        ]
        self.publish(activity, schema)
        student = self.student(activity)
        response = student.post(
            reverse("activity-student", args=[activity.slug]),
            {
                "revision": activity.revision,
                "question_id": activity_question(activity).pk,
                "section-once-TOTAL_FORMS": 1,
                "section-once-INITIAL_FORMS": 0,
                "section-once-0-comments": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(activity.responses.exists())

    def test_malformed_question_types_are_validation_errors(self):
        activity = self.create()
        schema = example_schema()
        schema["sections"][0]["fields"][0]["type"] = ["text"]
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]),
            self.editor_payload(activity, schema),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["schema_form"].errors)

    def test_numeric_minimum_must_fit_the_answer_field_and_examples_validate(self):
        from decimal import Decimal
        from .flexible import NUMERIC_LIMITS, SectionResponseForm, validate_schema
        from .flexible_plots import synthetic_entries

        for kind in ("currency_eur", "decimal", "integer"):
            with self.subTest(kind=kind):
                activity = self.create(f"Numeric {kind}")
                schema = example_schema()
                schema["sections"] = [schema["sections"][0]]
                schema["sections"][0]["fields"] = [
                    field("amount", kind, min_value=str(NUMERIC_LIMITS[kind] + 1))
                ]
                response = self.client.post(
                    reverse("research-edit-activity", args=[activity.slug]),
                    self.editor_payload(activity, schema),
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["schema_form"].errors)
                schema["sections"][0]["fields"][0]["min_value"] = str(
                    NUMERIC_LIMITS[kind]
                )
                question = self.publish(activity, validate_schema(schema))
                entries, _ = synthetic_entries(question)
                for _, answers, _ in entries:
                    self.assertLessEqual(
                        Decimal(str(answers["amount"])), NUMERIC_LIMITS[kind]
                    )
                    form = SectionResponseForm(answers, section=schema["sections"][0])
                    self.assertTrue(form.is_valid(), form.errors)

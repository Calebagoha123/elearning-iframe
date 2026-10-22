from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.settings import (
    DEV_SECRET_KEY,
    database_config,
    default_cookie_samesite,
    env_bool,
    require_production_secret,
    validate_production_password_hash,
)

from .models import ExportLog, Participant, Question, Response
from .services import (
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    housing_raw_payloads,
    housing_scatter_config,
    housing_scatter_points,
    save_housing_response,
    save_relationship_response,
    relationship_color_map,
    relationship_display_mode,
    relationship_participant_stacks,
    relationship_segmented_bars,
)


@override_settings(
    RESEARCH_ADMIN_PASSWORD="research-pass",
    NOMINATIM_VERIFY=False,
)
class IframeMvpTests(TestCase):
    def enter_iframe(self, participant_id="student-1"):
        return self.enter_activity_iframe(
            RELATIONSHIP_SLUG,
            pseudoname=participant_id,
        )

    def activity_iframe_url(self, activity_slug=RELATIONSHIP_SLUG):
        return reverse("activity-iframe-entry", args=[activity_slug])

    def enter_activity_iframe(self, activity_slug=RELATIONSHIP_SLUG, pseudoname="student-1"):
        return self.client.post(
            reverse("activity-iframe-entry", args=[activity_slug]),
            {"pseudoname": pseudoname},
            follow=True,
        )

    def participant(self, participant_id="student-1"):
        return Participant.objects.get(participant_id=participant_id)

    def add_relationship_responses(self, count):
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        for index in range(count):
            participant = get_or_create_participant(f"student-{index}")
            save_relationship_response(participant, question, "friend", "Friend")

    def add_housing_responses(self, count):
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        for index in range(count):
            participant = get_or_create_participant(f"student-{index}")
            save_housing_response(
                participant,
                question,
                {
                    "district": "North",
                    "monthly_rent": 900 + index * 10,
                    "rooms": 2,
                    "size_sqm": None,
                },
            )

    def test_activity_iframe_entry_prompts_for_moodle_pseudoname(self):
        response = self.client.get(self.activity_iframe_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relationship survey")
        self.assertContains(response, "Pseudoname")
        self.assertContains(response, "Use the pseudoname shown in Moodle.")

    def test_activity_iframe_entry_starts_requested_activity_without_landing(self):
        response = self.enter_activity_iframe()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relationship survey")
        self.assertNotContains(response, "Course activities")
        self.assertTrue(
            Participant.objects.filter(
                participant_id="student-1",
            ).exists()
        )

    def test_housing_iframe_entry_starts_housing_activity(self):
        response = self.enter_activity_iframe(HOUSING_SLUG, pseudoname="moodle-pseudo-42")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Housing data entry")
        self.assertNotContains(response, "Course activities")
        self.assertTrue(
            Participant.objects.filter(
                participant_id="moodle-pseudo-42",
            ).exists()
        )

    def test_activity_iframe_entry_requires_pseudoname(self):
        response = self.client.post(
            reverse("activity-iframe-entry", args=[RELATIONSHIP_SLUG]),
            {"pseudoname": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter your Moodle pseudoname.")
        self.assertEqual(Participant.objects.count(), 0)

    def test_bare_iframe_entry_is_not_available(self):
        response = self.client.get(
            "/iframe/?participant_id=student-1&sig=legacy-signature"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Participant.objects.count(), 0)

    def relationship_formset_payload(self, rows):
        payload = {
            "relationships-TOTAL_FORMS": str(len(rows)),
            "relationships-INITIAL_FORMS": "0",
            "relationships-MIN_NUM_FORMS": "0",
            "relationships-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            relationship = row.get("relationship", "")
            payload[f"relationships-{index}-relationship"] = relationship
            payload[f"relationships-{index}-income_comparison"] = row.get(
                "income_comparison",
                "same" if relationship else "",
            )
            payload[f"relationships-{index}-other_relationship"] = row.get("other_relationship", "")
        return payload

    def test_relationship_submission_saves_multiple_relationships_and_ignores_blank_rows(self):
        self.enter_iframe()
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        response = self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload(
                [
                    {"relationship": "friend"},
                    {"relationship": "other", "other_relationship": "Cousin"},
                    {},
                ]
            ),
        )

        self.assertEqual(response.status_code, 302)
        responses = Response.objects.filter(participant=self.participant(), question=question)
        self.assertEqual(responses.count(), 2)
        self.assertEqual(
            sorted(response.payload["relationship_label"] for response in responses),
            ["Cousin", "Friend"],
        )
        self.assertEqual(
            sorted(response.payload["income_comparison_label"] for response in responses),
            ["Same", "Same"],
        )

    def test_relationship_submission_replaces_previous_relationship_set(self):
        self.enter_iframe()
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload(
                [
                    {"relationship": "friend"},
                    {"relationship": "boss"},
                ]
            ),
        )
        self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload([{"relationship": "parent"}]),
        )

        responses = Response.objects.filter(participant=self.participant(), question=question)
        self.assertEqual(responses.count(), 1)
        self.assertEqual(responses.get().payload["relationship_label"], "Parent")
        self.assertEqual(responses.get().payload["income_comparison_value"], "same")

    def test_relationship_submission_requires_at_least_one_relationship(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload([{}]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add at least one relationship.")
        self.assertFalse(
            Response.objects.filter(participant=self.participant(), activity__slug=RELATIONSHIP_SLUG).exists()
        )

    def test_relationship_submission_rejects_other_text_without_relationship_selection(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload([{"relationship": "", "other_relationship": "Cousin"}]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select Other before entering a custom relationship.")
        self.assertFalse(
            Response.objects.filter(participant=self.participant(), activity__slug=RELATIONSHIP_SLUG).exists()
        )

    def test_relationship_form_progressively_enhances_other_field(self):
        self.enter_iframe()

        response = self.client.get(reverse("relationship-activity"))

        self.assertContains(response, 'class="relationship-other"', html=False)
        self.assertContains(response, 'data-add-relationship', html=False)
        self.assertNotContains(response, 'data-save-relationships disabled', html=False)
        self.assertContains(response, 'class="likert-scale"', html=False)
        self.assertContains(response, "Lower than theirs")

    def test_research_edit_relationship_changes_income_scale(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        response = self.client.post(
            reverse("research-edit-relationship"),
            {
                "question_text": question.question_text,
                "relationship_choices": "Friend\nParent\nSibling",
                "likert_points": "7",
                "label_7_much_lower": "Much lower than theirs",
                "label_7_lower": "Lower than theirs",
                "label_7_slightly_lower": "Slightly lower than theirs",
                "label_7_same": "Same",
                "label_7_slightly_higher": "Slightly higher than theirs",
                "label_7_higher": "Higher than theirs",
                "label_7_much_higher": "Much higher than theirs",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertFalse(question.is_active)
        new_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertEqual(new_question.version, question.version + 1)
        self.assertEqual(new_question.options_json["likert_points"], 7)

        self.enter_iframe()
        form_response = self.client.get(reverse("relationship-activity"))

        self.assertContains(form_response, "Slightly lower")
        self.assertContains(form_response, "Slightly higher")

    def test_research_edit_relationship_changes_relationship_options(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        response = self.client.post(
            reverse("research-edit-relationship"),
            {
                "question_text": question.question_text,
                "relationship_choices": "Friend\nCousin\nMentor",
                "likert_points": "3",
                "label_3_lower": "Lower than theirs",
                "label_3_same": "Same",
                "label_3_higher": "Higher than theirs",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertFalse(question.is_active)
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertEqual(
            [choice["label"] for choice in question.options_json["choices"]],
            ["Friend", "Cousin", "Mentor"],
        )

        self.enter_iframe()
        form_response = self.client.get(reverse("relationship-activity"))
        self.assertContains(form_response, "Cousin")
        self.assertContains(form_response, "Mentor")

    def test_research_edit_relationship_noops_when_unchanged(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        response = self.client.post(
            reverse("research-edit-relationship"),
            {
                "question_text": question.question_text,
                "relationship_choices": "\n".join(choice["label"] for choice in question.options_json["choices"]),
                "likert_points": "5",
                "label_5_much_lower": "Much lower than theirs",
                "label_5_lower": "Lower than theirs",
                "label_5_same": "Same",
                "label_5_higher": "Higher than theirs",
                "label_5_much_higher": "Much higher than theirs",
            },
            follow=True,
        )

        question.refresh_from_db()
        self.assertTrue(question.is_active)
        self.assertEqual(active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG).pk, question.pk)
        self.assertContains(response, "No changes to save.")

    def test_relationship_results_are_visible_without_threshold(self):
        self.enter_iframe()
        self.add_relationship_responses(1)

        response = self.client.get(reverse("relationship-results"))

        self.assertContains(response, "Relationship results")
        self.assertContains(response, "Friend")
        self.assertContains(response, "Income comparison")
        self.assertNotContains(response, "Results unlock")

    def test_relationship_results_show_many_relationships_from_one_participant(self):
        self.enter_iframe()
        self.client.post(
            reverse("relationship-activity"),
            self.relationship_formset_payload(
                [
                    {"relationship": "friend"},
                    {"relationship": "boss"},
                    {"relationship": "parent"},
                    {"relationship": "sibling"},
                    {"relationship": "colleague"},
                ]
            ),
        )

        response = self.client.get(reverse("relationship-results"))

        self.assertContains(response, "Relationship results")
        self.assertContains(response, "Friend")
        self.assertNotContains(response, "Results unlock")

    def test_relationship_results_show_word_cloud(self):
        self.enter_iframe()
        self.add_relationship_responses(2)

        response = self.client.get(reverse("relationship-results"))

        self.assertContains(response, "Relationship results")
        self.assertContains(response, "Friend")

    def test_relationship_edit_starts_new_result_version(self):
        self.add_relationship_responses(2)
        old_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        self.client.post(
            reverse("research-edit-relationship"),
            {
                "question_text": "Updated relationship wording?",
                "relationship_choices": "Friend\nParent\nSibling",
                "likert_points": "3",
                "label_3_lower": "Lower than theirs",
                "label_3_same": "Same",
                "label_3_higher": "Higher than theirs",
            },
        )

        new_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertNotEqual(new_question.pk, old_question.pk)
        self.assertEqual(Response.objects.filter(question=old_question).count(), 2)

        response = self.client.get(reverse("research-relationship-plots"))
        self.assertContains(response, "No answers yet.")
        self.assertEqual(response.context["response_count"], 0)
        self.assertTrue(all(row["count"] == 0 for chart in response.context["distributions"] for row in chart["rows"]))

    def test_seed_mvp_does_not_reactivate_old_question_versions(self):
        old_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        self.client.post(
            reverse("research-edit-relationship"),
            {
                "question_text": "Updated relationship wording?",
                "relationship_choices": "Friend\nParent\nSibling",
                "likert_points": "3",
                "label_3_lower": "Lower than theirs",
                "label_3_same": "Same",
                "label_3_higher": "Higher than theirs",
            },
        )
        new_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)

        call_command("seed_mvp")

        old_question.refresh_from_db()
        new_question.refresh_from_db()
        self.assertFalse(old_question.is_active)
        self.assertTrue(new_question.is_active)
        self.assertEqual(
            Question.objects.filter(
                activity__slug=RELATIONSHIP_SLUG,
                slug=RELATIONSHIP_QUESTION_SLUG,
                is_active=True,
            ).count(),
            1,
        )

    def test_relationship_display_mode_defaults_to_cohort(self):
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertEqual(relationship_display_mode(question), "cohort")

    def test_research_relationship_plot_page_updates_display_mode(self):
        self.add_relationship_responses(2)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        response = self.client.post(
            reverse("research-relationship-plots"),
            {"display_mode": "both"},
            follow=True,
        )

        active = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertEqual(relationship_display_mode(active), "both")
        self.assertContains(response, "Relationship chart updated.")

    def test_research_relationship_plot_page_rejects_unknown_mode(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        self.client.post(reverse("research-relationship-plots"), {"display_mode": "garbage"})

        active = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        self.assertEqual(relationship_display_mode(active), "cohort")

    def test_segmented_bars_side_by_side_includes_yours_and_cohort(self):
        # Two participants give responses; ask for "both" mode as participant A.
        self.enter_iframe(participant_id="student-a")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "friend"}]))
        participant_a = Participant.objects.get(participant_id="student-a")

        self.enter_iframe(participant_id="student-b")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "parent"}]))

        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        bars = relationship_segmented_bars(question, participant=participant_a, mode="both")

        # Every column gets both stacks defined (even if empty for some columns).
        self.assertTrue(all("yours" in col and "cohort" in col for col in bars["columns"]))
        yours_total = sum(col["yours"]["total"] for col in bars["columns"])
        cohort_total = sum(col["cohort"]["total"] for col in bars["columns"])
        # Cohort includes everyone (participant A counts too).
        self.assertEqual(yours_total, 1)
        self.assertEqual(cohort_total, 2)
        self.assertEqual(bars["mode"], "both")

    def test_segmented_bars_yours_only_excludes_cohort(self):
        self.enter_iframe(participant_id="student-a")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "friend"}]))
        participant_a = Participant.objects.get(participant_id="student-a")

        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        bars = relationship_segmented_bars(question, participant=participant_a, mode="yours")

        cohort_total = sum(col["cohort"]["total"] for col in bars["columns"])
        self.assertEqual(cohort_total, 0)
        yours_total = sum(col["yours"]["total"] for col in bars["columns"])
        self.assertEqual(yours_total, 1)

    def test_relationship_participant_stacks_are_keyed_per_respondent(self):
        self.enter_iframe(participant_id="student-a")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "friend"}]))
        self.enter_iframe(participant_id="student-b")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "parent"}]))

        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        stacks = relationship_participant_stacks(question)

        self.assertEqual(sorted(stacks.keys()), ["student-a", "student-b"])
        a_labels = {seg["label"] for stack in stacks["student-a"].values() for seg in stack["segments"]}
        self.assertEqual(a_labels, {"Friend"})
        a_total = sum(stack["total"] for stack in stacks["student-a"].values())
        self.assertEqual(a_total, 1)

    def test_research_relationship_plot_page_lists_respondents_for_overlay(self):
        self.enter_iframe(participant_id="student-a")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "friend"}]))
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        response = self.client.get(reverse("research-relationship-plots"))
        self.assertContains(response, "Highlight a respondent")
        self.assertEqual(response.context["focus_choices"], ["student-a"])
        self.assertContains(response, "student-a")

    def housing_form_payload(self, district="North Berlin", rent="1200.00", rooms="2", size=""):
        return {
            "district": district,
            "district_osm_type": "relation",
            "district_osm_id": "62422",
            "district_display_name": district,
            "monthly_rent": rent,
            "rooms": rooms,
            "size_sqm": size,
        }

    def test_housing_entries_allow_multiple_rows(self):
        self.enter_iframe()

        for district in ["North Berlin", "South Berlin"]:
            response = self.client.post(
                reverse("housing-activity"),
                self.housing_form_payload(district=district),
            )
            self.assertEqual(response.status_code, 302)

        self.assertEqual(
            Response.objects.filter(participant=self.participant(), activity__slug=HOUSING_SLUG).count(),
            2,
        )

    def test_housing_results_compare_without_threshold(self):
        self.enter_iframe()
        self.add_housing_responses(1)

        response = self.client.get(reverse("housing-results"))

        self.assertContains(response, "Housing comparison")
        self.assertContains(response, "Monthly rent")
        self.assertContains(response, "Number of rooms")
        self.assertContains(response, "Cohort entries")

    def test_housing_submission_redirects_back_to_entry_form(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("housing-activity"),
            self.housing_form_payload(),
        )

        self.assertRedirects(response, reverse("housing-activity"))

    def schema_formset_payload(self, fields, prefix="fields"):
        payload = {
            f"{prefix}-TOTAL_FORMS": str(len(fields)),
            f"{prefix}-INITIAL_FORMS": str(len(fields)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, field in enumerate(fields):
            payload[f"{prefix}-{index}-key"] = field.get("key", "")
            payload[f"{prefix}-{index}-label"] = field["label"]
            payload[f"{prefix}-{index}-field_type"] = field["type"]
            if field.get("required"):
                payload[f"{prefix}-{index}-required"] = "on"
            payload[f"{prefix}-{index}-help_text"] = field.get("help_text", "")
            payload[f"{prefix}-{index}-options_text"] = "\n".join(
                option["label"] for option in field.get("options", [])
            )
            payload[f"{prefix}-{index}-ORDER"] = str(index)
        return payload

    def default_housing_schema_payload(self):
        return [
            {"key": "district", "label": "District", "type": "place", "required": True,
             "help_text": "Start typing a real district or city, then pick a suggestion."},
            {"key": "monthly_rent", "label": "Monthly rent", "type": "currency_eur",
             "required": True, "help_text": "Total monthly rent in euros."},
            {"key": "rooms", "label": "Number of rooms", "type": "decimal", "required": True,
             "help_text": "Use .5 for a half room (e.g. 2.5)."},
            {"key": "size_sqm", "label": "Size (m²)", "type": "decimal", "required": False,
             "help_text": "Optional. Floor area in square metres."},
        ]

    def test_research_edit_housing_noops_when_unchanged(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        payload = {
            "question_text": question.question_text,
            "x_axis": "rooms",
            "y_axis": "monthly_rent",
            **self.schema_formset_payload(self.default_housing_schema_payload()),
        }
        response = self.client.post(reverse("research-edit-housing"), payload, follow=True)

        question.refresh_from_db()
        self.assertTrue(question.is_active)
        self.assertEqual(active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG).pk, question.pk)
        self.assertContains(response, "No changes to save.")

    def test_research_edit_housing_save_is_available_without_javascript(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        response = self.client.get(reverse("research-edit-housing"))

        self.assertContains(response, 'data-save-changes')
        self.assertNotContains(response, 'data-save-changes disabled')

    def test_research_plot_page_updates_axes_in_place(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        response = self.client.post(
            reverse("research-housing-plots"),
            {
                "x_axis_mode": "single",
                "x_axis_numerator": "monthly_rent",
                "x_axis_denominator": "",
                "y_axis_mode": "single",
                "y_axis_numerator": "rooms",
                "y_axis_denominator": "",
            },
            follow=True,
        )

        question.refresh_from_db()
        active = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        self.assertEqual(active.pk, question.pk)
        self.assertEqual(active.version, question.version)
        self.assertTrue(active.is_active)
        self.assertEqual(
            active.options_json["viz"],
            {
                "x_axis": {"numerator": "monthly_rent", "denominator": "", "label": ""},
                "y_axis": {"numerator": "rooms", "denominator": "", "label": ""},
            },
        )
        self.assertContains(response, "Scatter plot axes updated.")

    def test_research_plot_page_supports_ratio_axes_with_custom_label(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        self.client.post(
            reverse("research-housing-plots"),
            {
                "x_axis_mode": "ratio",
                "x_axis_numerator": "monthly_rent",
                "x_axis_denominator": "size_sqm",
                "x_axis_label": "Rent per m²",
                "y_axis_mode": "single",
                "y_axis_numerator": "rooms",
                "y_axis_denominator": "",
                "y_axis_label": "ignored when single",
            },
        )

        active = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        self.assertEqual(
            active.options_json["viz"]["x_axis"],
            {"numerator": "monthly_rent", "denominator": "size_sqm", "label": "Rent per m²"},
        )
        # Single-axis labels are dropped, since custom labels only apply to ratios.
        self.assertEqual(active.options_json["viz"]["y_axis"]["label"], "")

        from .services import housing_axis_label
        self.assertEqual(housing_axis_label(active, active.options_json["viz"]["x_axis"]), "Rent per m²")

    def test_legacy_rent_per_sqm_axis_string_still_renders(self):
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        options = dict(question.options_json or {})
        options["viz"] = {"x_axis": "rent_per_sqm", "y_axis": "rooms"}
        Question.objects.filter(pk=question.pk).update(options_json=options)
        question.refresh_from_db()

        config = housing_scatter_config(question)
        self.assertEqual(
            config["x_axis"],
            {"numerator": "monthly_rent", "denominator": "size_sqm", "label": ""},
        )
        self.assertEqual(
            config["y_axis"],
            {"numerator": "rooms", "denominator": "", "label": ""},
        )

    def test_research_dashboard_and_csv_require_login(self):
        dashboard = self.client.get(reverse("research-dashboard"))
        export = self.client.get(reverse("research-export-csv"))

        self.assertEqual(dashboard.status_code, 302)
        self.assertEqual(export.status_code, 302)

    def test_research_dashboard_reports_exact_counts(self):
        self.add_relationship_responses(2)
        self.add_housing_responses(3)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        response = self.client.get(reverse("research-dashboard"))

        self.assertContains(response, "Participants")
        self.assertContains(response, "<strong>3</strong>", html=True)
        self.assertContains(response, "Relationship survey")
        self.assertContains(response, "Housing data entry")
        self.assertContains(response, "<strong>2</strong>", html=True)
        self.assertContains(response, reverse("research-edit-relationship"))
        self.assertContains(response, reverse("research-edit-housing"))
        self.assertContains(response, reverse("research-activity-responses", args=[RELATIONSHIP_SLUG]))
        self.assertContains(response, reverse("research-activity-responses", args=[HOUSING_SLUG]))

    def test_research_plot_pages_show_cohort_results(self):
        self.add_relationship_responses(2)
        self.add_housing_responses(2)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        relationship = self.client.get(reverse("research-relationship-plots"))
        housing = self.client.get(reverse("research-housing-plots"))

        self.assertContains(relationship, "Relationship survey")
        self.assertContains(relationship, "Friend")
        self.assertContains(housing, "Housing data entry")
        self.assertContains(housing, "data-flexible-scatter")
        self.assertTemplateUsed(relationship, "research/flexible_plots.html")
        self.assertTemplateUsed(housing, "research/flexible_plots.html")

    def test_housing_raw_payloads_hide_identity_unless_requested(self):
        self.add_housing_responses(2)
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        anonymous = housing_raw_payloads(question, None)
        self.assertTrue(all("participant_id" not in row for row in anonymous))

        identified = housing_raw_payloads(question, None, include_identity=True)
        self.assertEqual(
            sorted(row["participant_id"] for row in identified),
            ["student-0", "student-1"],
        )

    def test_research_housing_plot_page_lists_respondents_for_highlight(self):
        self.add_housing_responses(2)  # creates student-0 and student-1
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        response = self.client.get(reverse("research-housing-plots"))
        self.assertContains(response, "Highlight a respondent")
        self.assertEqual(response.context["focus_choices"], ["student-0", "student-1"])
        self.assertTrue(all("participant_id" not in point for point in response.context["scatter_points"]))
        self.assertContains(response, "student-1")

    def test_student_housing_results_do_not_leak_other_pseudonames(self):
        self.add_housing_responses(2)  # creates student-0 and student-1
        self.enter_iframe(participant_id="student-0")

        response = self.client.get(reverse("housing-results"))
        self.assertNotContains(response, '"participant_id"')  # no identity key in the payload JSON
        self.assertNotContains(response, "student-1")

    def test_student_summary_combines_both_visualizations(self):
        self.enter_iframe(participant_id="student-1")
        self.client.post(reverse("relationship-activity"), self.relationship_formset_payload([{"relationship": "friend"}]))
        self.client.post(reverse("housing-activity"), self.housing_form_payload())

        response = self.client.get(reverse("student-summary"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your results")
        self.assertContains(response, "data-segmented-chart-host")  # relationship stacked bar
        self.assertContains(response, "data-scatter-plot")  # housing scatter
        # The summary is a student page: no research controls, no leaked identities.
        self.assertNotContains(response, "What students see")  # display-mode picker label
        self.assertNotContains(response, "Highlight a respondent")  # housing focus-picker label
        self.assertNotContains(response, '"participant_id"')

    def test_student_summary_requires_a_session(self):
        response = self.client.get(reverse("student-summary"))
        self.assertEqual(response.status_code, 403)

    def test_summary_iframe_entry_lands_on_summary(self):
        response = self.enter_activity_iframe("summary", pseudoname="moodle-pseudo-7")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your results")
        self.assertContains(response, "data-scatter-plot")

    def test_seed_demo_responses_populates_visualization_data(self):
        call_command(
            "seed_demo_responses",
            participants=6,
            housing_entries=12,
            seed=7,
        )
        relationship_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        housing_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        self.assertEqual(Participant.objects.filter(participant_id__startswith="demo-").count(), 6)
        self.assertGreater(Response.objects.filter(question=relationship_question).count(), 6)
        self.assertEqual(Response.objects.filter(question=housing_question).count(), 12)
        points = housing_scatter_points(
            housing_question,
            None,
            "rooms",
            "monthly_rent",
        )
        self.assertGreater(len(points), 0)
        self.assertTrue(points[0]["district"])

    def test_research_login_and_csv_export(self):
        self.add_relationship_responses(1)

        login = self.client.post(
            reverse("research-login"),
            {"password": "research-pass"},
            follow=True,
        )
        export = self.client.get(reverse("research-export-csv"))

        self.assertEqual(login.status_code, 200)
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv")
        self.assertContains(export, "participant_id,activity_slug")
        self.assertEqual(ExportLog.objects.count(), 1)

    def test_housing_csv_export_keeps_historical_schema_columns(self):
        self.add_housing_responses(1)
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        old_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        schema = [
            field
            for field in self.default_housing_schema_payload()
            if field["key"] != "size_sqm"
        ]

        self.client.post(
            reverse("research-edit-housing"),
            {
                "question_text": old_question.question_text,
                "x_axis": "rooms",
                "y_axis": "monthly_rent",
                **self.schema_formset_payload(schema),
            },
        )
        export = self.client.get(f"{reverse('research-export-csv')}?activity=housing")

        header = export.content.decode().splitlines()[0]
        self.assertIn("size_sqm", header)

    def test_research_login_rejects_external_next_redirect(self):
        response = self.client.post(
            f"{reverse('research-login')}?next=https://example.com/steal",
            {"password": "research-pass", "next": "https://example.com/steal"},
        )

        self.assertRedirects(response, reverse("research-dashboard"), fetch_redirect_response=False)

    def test_research_login_accepts_hashed_password(self):
        from django.contrib.auth.hashers import make_password
        with self.settings(
            RESEARCH_ADMIN_PASSWORD="not-the-real-password",
            RESEARCH_ADMIN_PASSWORD_HASH=make_password("hashed-pass-42"),
        ):
            response = self.client.post(
                reverse("research-login"),
                {"password": "hashed-pass-42"},
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Dashboard")

    def test_research_login_rejects_plaintext_when_hash_is_set(self):
        from django.contrib.auth.hashers import make_password
        with self.settings(
            RESEARCH_ADMIN_PASSWORD="plaintext-pass",
            RESEARCH_ADMIN_PASSWORD_HASH=make_password("hashed-pass-42"),
        ):
            response = self.client.post(
                reverse("research-login"),
                {"password": "plaintext-pass"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "not accepted")

    def test_research_login_accepts_csrf_checked_browser_post(self):
        client = Client(enforce_csrf_checks=True)
        login_page = client.get(reverse("research-login"))

        response = client.post(
            reverse("research-login"),
            {
                "password": "research-pass",
                "csrfmiddlewaretoken": login_page.cookies["csrftoken"].value,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")

    def test_healthz_returns_ok_when_db_reachable(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(b"ok", response.content)

    def test_seeded_questions_exist(self):
        self.assertTrue(
            Question.objects.filter(activity__slug=RELATIONSHIP_SLUG, slug=RELATIONSHIP_QUESTION_SLUG).exists()
        )
        self.assertTrue(Question.objects.filter(activity__slug=HOUSING_SLUG, slug=HOUSING_QUESTION_SLUG).exists())

    def test_default_relationship_options_get_distinct_colors(self):
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        colors = relationship_color_map(question)

        self.assertEqual(len(colors), len(set(colors.values())))

    def test_housing_form_renders_dynamic_typed_fields(self):
        self.enter_iframe()

        response = self.client.get(reverse("housing-activity"))

        self.assertContains(response, 'data-place-input="district"')
        self.assertContains(response, 'data-currency="eur"')
        self.assertContains(response, 'inputmode="decimal"')
        self.assertContains(response, "€")

    def test_housing_rejects_non_numeric_rent(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("housing-activity"),
            self.housing_form_payload(rent="abc"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Response.objects.filter(activity__slug=HOUSING_SLUG).exists()
        )

    def test_housing_rejects_negative_rent(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("housing-activity"),
            self.housing_form_payload(rent="-50"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Response.objects.filter(activity__slug=HOUSING_SLUG).exists()
        )

    def test_housing_rejects_unverified_place(self):
        self.enter_iframe()

        payload = self.housing_form_payload()
        payload["district_osm_type"] = ""
        payload["district_osm_id"] = ""

        response = self.client.post(reverse("housing-activity"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dropdown suggestions")
        self.assertFalse(
            Response.objects.filter(activity__slug=HOUSING_SLUG).exists()
        )

    def test_research_edit_housing_changes_schema_and_versions(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        old_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        new_schema = self.default_housing_schema_payload()
        new_schema.append({
            "key": "",
            "label": "Year built",
            "type": "integer",
            "required": False,
            "help_text": "",
        })

        response = self.client.post(
            reverse("research-edit-housing"),
            {
                "question_text": old_question.question_text,
                "x_axis": "rooms",
                "y_axis": "monthly_rent",
                **self.schema_formset_payload(new_schema),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        old_question.refresh_from_db()
        self.assertFalse(old_question.is_active)
        new_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        self.assertEqual(new_question.version, old_question.version + 1)
        keys = [field["key"] for field in new_question.options_json["fields"]]
        self.assertIn("year_built", keys)

    def test_student_can_delete_their_own_housing_entry(self):
        self.enter_iframe()
        self.client.post(reverse("housing-activity"), self.housing_form_payload())
        entry = Response.objects.filter(activity__slug=HOUSING_SLUG).get()

        response = self.client.post(reverse("housing-delete-entry", args=[entry.pk]))

        self.assertRedirects(response, reverse("housing-activity"))
        self.assertFalse(Response.objects.filter(pk=entry.pk).exists())

    def test_student_cannot_delete_someone_elses_housing_entry(self):
        # Participant A creates an entry
        self.enter_iframe(participant_id="student-a")
        self.client.post(reverse("housing-activity"), self.housing_form_payload())
        entry = Response.objects.filter(activity__slug=HOUSING_SLUG).get()

        # Participant B tries to delete it
        self.enter_iframe(participant_id="student-b")
        self.client.post(reverse("housing-delete-entry", args=[entry.pk]))

        self.assertTrue(Response.objects.filter(pk=entry.pk).exists())

    def test_research_edit_housing_preserves_stable_keys_on_rename(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        old_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        schema = self.default_housing_schema_payload()
        for field in schema:
            if field["key"] == "district":
                field["label"] = "Neighbourhood"

        self.client.post(
            reverse("research-edit-housing"),
            {
                "question_text": old_question.question_text,
                "x_axis": "rooms",
                "y_axis": "monthly_rent",
                **self.schema_formset_payload(schema),
            },
        )

        new_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        first = new_question.options_json["fields"][0]
        self.assertEqual(first["key"], "district")
        self.assertEqual(first["label"], "Neighbourhood")


class SettingsTests(SimpleTestCase):
    def test_env_bool_reads_common_truthy_and_falsey_values(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(env_bool("MISSING_BOOLEAN_FOR_TEST", True))
            self.assertFalse(env_bool("MISSING_BOOLEAN_FOR_TEST", False))
        with patch.dict("os.environ", {"BOOLEAN_FOR_TEST": "yes"}):
            self.assertTrue(env_bool("BOOLEAN_FOR_TEST", False))
        with patch.dict("os.environ", {"BOOLEAN_FOR_TEST": "0"}):
            self.assertFalse(env_bool("BOOLEAN_FOR_TEST", True))

    def test_cookie_samesite_default_matches_http_local_and_https_iframe_modes(self):
        self.assertEqual(default_cookie_samesite(debug=True), "Lax")
        self.assertEqual(default_cookie_samesite(debug=False), "None")

    def test_test_environment_uses_http_compatible_cookies(self):
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertFalse(settings.SESSION_COOKIE_SECURE)

    def test_production_secrets_reject_missing_or_dev_values(self):
        with patch("config.settings.DEBUG", False):
            with self.assertRaises(ImproperlyConfigured):
                require_production_secret("DJANGO_SECRET_KEY", "", DEV_SECRET_KEY)
            require_production_secret("DJANGO_SECRET_KEY", "custom-secret", DEV_SECRET_KEY)

    def test_production_password_hash_rejects_malformed_hashes(self):
        with patch("config.settings.DEBUG", False):
            with self.assertRaises(ImproperlyConfigured):
                validate_production_password_hash("dummy")
            with self.assertRaises(ImproperlyConfigured):
                validate_production_password_hash("pbkdf2_sha256$not-an-int$salt$value")
            validate_production_password_hash(make_password("research-pass"))

    def test_database_url_decodes_credentials_and_preserves_query_options(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgres://elearning:p%40ss%2Fword@db:5432/learning%20db?sslmode=require&connect_timeout=10",
                "DJANGO_DB_POOL": "0",
            },
            clear=False,
        ):
            config = database_config()

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "learning db")
        self.assertEqual(config["USER"], "elearning")
        self.assertEqual(config["PASSWORD"], "p@ss/word")
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")
        self.assertEqual(config["OPTIONS"]["connect_timeout"], "10")

from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.settings import default_cookie_samesite, env_bool

from .models import ExportLog, Participant, Question, Response
from .services import (
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    make_iframe_signature,
    save_housing_response,
    save_relationship_response,
    relationship_color_map,
)


@override_settings(
    IFRAME_SIGNING_SECRET="test-secret",
    RESEARCH_ADMIN_PASSWORD="research-pass",
)
class IframeMvpTests(TestCase):
    def iframe_url(self, participant_id="student-1"):
        sig = make_iframe_signature("", participant_id)
        return f"{reverse('iframe-entry')}?participant_id={participant_id}&sig={sig}"

    def enter_iframe(self, participant_id="student-1"):
        return self.client.get(self.iframe_url(participant_id), follow=True)

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

    def test_signed_iframe_entry_creates_session_participant(self):
        response = self.enter_iframe()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activities")
        self.assertTrue(
            Participant.objects.filter(
                participant_id="student-1",
            ).exists()
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

    def test_invalid_iframe_signature_is_rejected(self):
        response = self.client.get(
            f"{reverse('iframe-entry')}?participant_id=student-1&sig=bad"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Participant.objects.count(), 0)

    def test_iframe_signature_does_not_allow_participant_collision(self):
        collision_signature = make_iframe_signature("", "student:1")

        response = self.client.get(
            f"{reverse('iframe-entry')}?participant_id=1&sig={collision_signature}"
        )

        self.assertEqual(response.status_code, 403)
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

    def test_relationship_form_hides_other_field_until_other_is_selected(self):
        self.enter_iframe()

        response = self.client.get(reverse("relationship-activity"))

        self.assertContains(response, 'class="relationship-other is-hidden"', html=False)
        self.assertContains(response, 'data-add-relationship', html=False)
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
        self.assertContains(response, "No responses yet.")
        self.assertNotContains(response, "Friend")

    def test_housing_entries_allow_multiple_rows(self):
        self.enter_iframe()

        for district in ["North", "South"]:
            response = self.client.post(
                reverse("housing-activity"),
                {
                    "district": district,
                    "monthly_rent": "1200.00",
                    "rooms": "2",
                    "size_sqm": "",
                },
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
        self.assertContains(response, "Total monthly rent")
        self.assertContains(response, "Number of rooms")
        self.assertContains(response, "Cohort entries")

    def test_housing_submission_redirects_back_to_entry_form(self):
        self.enter_iframe()

        response = self.client.post(
            reverse("housing-activity"),
            {
                "district": "North",
                "monthly_rent": "1200.00",
                "rooms": "2",
                "size_sqm": "",
            },
        )

        self.assertRedirects(response, reverse("housing-activity"))

    def test_research_edit_housing_noops_when_unchanged(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        response = self.client.post(
            reverse("research-edit-housing"),
            {
                "question_text": question.question_text,
                "x_axis": "rooms",
                "y_axis": "monthly_rent",
            },
            follow=True,
        )

        question.refresh_from_db()
        self.assertTrue(question.is_active)
        self.assertEqual(active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG).pk, question.pk)
        self.assertContains(response, "No changes to save.")

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
        self.assertContains(response, reverse("research-relationship-plots"))
        self.assertContains(response, reverse("research-housing-plots"))

    def test_research_plot_pages_show_cohort_results(self):
        self.add_relationship_responses(2)
        self.add_housing_responses(2)
        self.client.post(reverse("research-login"), {"password": "research-pass"})

        relationship = self.client.get(reverse("research-relationship-plots"))
        housing = self.client.get(reverse("research-housing-plots"))

        self.assertContains(relationship, "Relationship results")
        self.assertContains(relationship, "Friend")
        self.assertContains(housing, "Housing comparison")
        self.assertContains(housing, "data-scatter-toggle")

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

    def test_research_login_rejects_external_next_redirect(self):
        response = self.client.post(
            f"{reverse('research-login')}?next=https://example.com/steal",
            {"password": "research-pass", "next": "https://example.com/steal"},
        )

        self.assertRedirects(response, reverse("research-dashboard"), fetch_redirect_response=False)

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

    def test_seeded_questions_exist(self):
        self.assertTrue(
            Question.objects.filter(activity__slug=RELATIONSHIP_SLUG, slug=RELATIONSHIP_QUESTION_SLUG).exists()
        )
        self.assertTrue(Question.objects.filter(activity__slug=HOUSING_SLUG, slug=HOUSING_QUESTION_SLUG).exists())

    def test_default_relationship_options_get_distinct_colors(self):
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        colors = relationship_color_map(question)

        self.assertEqual(len(colors), len(set(colors.values())))


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

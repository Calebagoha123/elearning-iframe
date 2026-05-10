from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.settings import default_cookie_samesite, env_bool

from .models import Course, ExportLog, Participant, Question, Response
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
)


@override_settings(
    IFRAME_SIGNING_SECRET="test-secret",
    RESEARCH_ADMIN_PASSWORD="research-pass",
    VISUALIZATION_MIN_RESPONSES=5,
)
class IframeMvpTests(TestCase):
    def iframe_url(self, course_id="course-a", participant_id="student-1"):
        sig = make_iframe_signature(course_id, participant_id)
        return f"{reverse('iframe-entry')}?course_id={course_id}&participant_id={participant_id}&sig={sig}"

    def enter_iframe(self, course_id="course-a", participant_id="student-1"):
        return self.client.get(self.iframe_url(course_id, participant_id), follow=True)

    def participant(self, course_id="course-a", participant_id="student-1"):
        return Participant.objects.get(course__course_id=course_id, participant_id=participant_id)

    def add_relationship_responses(self, count):
        question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        for index in range(count):
            participant = get_or_create_participant("course-a", f"student-{index}")
            save_relationship_response(participant, question, "friend", "Friend")

    def add_housing_responses(self, count):
        question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
        for index in range(count):
            participant = get_or_create_participant("course-a", f"student-{index}")
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
        self.assertContains(response, "Course activities")
        self.assertTrue(Course.objects.filter(course_id="course-a").exists())
        self.assertTrue(
            Participant.objects.filter(
                course__course_id="course-a",
                participant_id="student-1",
            ).exists()
        )

    def test_invalid_iframe_signature_is_rejected(self):
        response = self.client.get(
            f"{reverse('iframe-entry')}?course_id=course-a&participant_id=student-1&sig=bad"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Participant.objects.count(), 0)

    def test_iframe_signature_does_not_allow_colon_delimiter_collision(self):
        collision_signature = make_iframe_signature("course-a", "student:1")

        response = self.client.get(
            f"{reverse('iframe-entry')}?course_id=course-a:student&participant_id=1&sig={collision_signature}"
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
            payload[f"relationships-{index}-relationship"] = row.get("relationship", "")
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

    def test_relationship_results_are_locked_until_threshold(self):
        self.enter_iframe()
        self.add_relationship_responses(4)

        response = self.client.get(reverse("relationship-results"))

        self.assertContains(response, "Results unlock at 5 participants.")

    def test_relationship_results_are_locked_when_one_participant_submits_many_relationships(self):
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

        self.assertContains(response, "Results unlock at 5 participants.")

    def test_relationship_results_show_word_cloud_at_threshold(self):
        self.enter_iframe()
        self.add_relationship_responses(settings.VISUALIZATION_MIN_RESPONSES)

        response = self.client.get(reverse("relationship-results"))

        self.assertContains(response, "Relationship results")
        self.assertContains(response, "Friend")

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

    def test_housing_results_compare_at_threshold(self):
        self.enter_iframe()
        self.add_housing_responses(5)

        response = self.client.get(reverse("housing-results"))

        self.assertContains(response, "Housing comparison")
        self.assertContains(response, "North")
        self.assertContains(response, "Cohort")

    def test_housing_results_are_locked_when_one_participant_submits_many_entries(self):
        self.enter_iframe()
        for index in range(5):
            self.client.post(
                reverse("housing-activity"),
                {
                    "district": f"District {index}",
                    "monthly_rent": "1200.00",
                    "rooms": "2",
                    "size_sqm": "",
                },
            )

        response = self.client.get(reverse("housing-results"))

        self.assertContains(response, "Results unlock at 5 participants.")

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
        self.assertContains(response, "Responses")
        self.assertContains(response, "<strong>5</strong>", html=True)
        self.assertContains(response, "Relationship survey")
        self.assertContains(response, "Housing data entry")

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
        self.assertContains(export, "course_id,participant_id,activity_slug")
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

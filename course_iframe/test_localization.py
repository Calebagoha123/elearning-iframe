import json
from django import forms
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Activity, Participant, Response
from .activity_builder import activity_question
from .flexible import schema_for_question, response_answers
from .test_activity_builder import example_schema


def variant(schema, code):
    prefix = {"de": "Deutsch", "nl": "Nederlands", "fr": "Français", "en": "English"}[
        code
    ]
    for section in schema["sections"]:
        section.setdefault("translations", {})[code] = {
            key: prefix + " " + section[key] if section[key] else ""
            for key in ("title", "instructions", "add_label")
        }
        for field in section["fields"]:
            field.setdefault("translations", {})[code] = {
                key: prefix + " " + field[key] if field[key] else ""
                for key in ("label", "help_text")
            }
            for choice in field["choices"]:
                choice.setdefault("translations", {})[code] = {
                    "label": prefix + " " + choice["label"]
                }
    return schema


@override_settings(RESEARCH_ADMIN_PASSWORD="research-pass", NOMINATIM_VERIFY=False)
class LanguageVersionsTests(TestCase):
    def setUp(self):
        self.client.post(reverse("research-login"), {"password": "research-pass"})

    def create(self, source="en"):
        self.client.post(
            reverse("research-add-activity"),
            {
                "title": "Course title",
                "description": "Course instructions",
                "source_language": source,
            },
        )
        return Activity.objects.filter(activity_type="builder").latest("pk")

    def editor_data(self, activity, schema=None):
        page = self.client.get(reverse("research-edit-activity", args=[activity.slug]))
        data = {}
        for field in page.context["activity_settings"]:
            if isinstance(field.field, forms.BooleanField):
                if field.value():
                    data[field.html_name] = "on"
            elif field.value() is not None:
                data[field.html_name] = field.value()
        data["activity-is_active"] = "on"
        data["schema"] = json.dumps(schema or page.context["builder_schema"])
        return data

    def save(self, activity, schema, translations=None):
        data = self.editor_data(activity, schema)
        if translations is not None:
            data["activity-activity_translations"] = json.dumps(translations)
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]), data
        )
        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        return activity_question(activity)

    def ready(self):
        activity = self.create()
        schema = example_schema()
        for code in ["nl", "de", "fr"]:
            variant(schema, code)
        translations = {
            code: {
                "title": code + " title",
                "description": code + " instructions",
                "ui": {},
            }
            for code in ["nl", "de", "fr"]
        }
        question = self.save(activity, schema, translations)
        return activity, question

    def visitor(self, activity, code=None):
        visitor = Client()
        url = reverse("activity-iframe-entry", args=[activity.slug])
        response = visitor.get(url, {"lang": code} if code else {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(visitor.get(response.url).status_code, 200)
        return visitor

    def answers(self, activity, code="en"):
        return {
            "question_id": activity_question(activity).pk,
            "revision": activity.revision,
            "language": code,
            "section-once-TOTAL_FORMS": "1",
            "section-once-INITIAL_FORMS": "0",
            "section-once-0-comments": "Participant text stays unchanged",
            "section-once-0-preferences": ["1"],
            "section-records-TOTAL_FORMS": "1",
            "section-records-INITIAL_FORMS": "0",
            "section-records-0-category": "1",
            "section-records-0-rating": "3",
            "section-records-0-cost": "50",
            "section-records-0-size": "2",
        }

    def test_parallel_links_translate_all_authored_fields_and_keep_answer_codes(self):
        activity, question = self.ready()
        for code, prefix in [
            ("en", ""),
            ("nl", "Nederlands "),
            ("de", "Deutsch "),
            ("fr", "Français "),
        ]:
            with self.subTest(code=code):
                visitor = self.visitor(activity, code)
                page = visitor.get(
                    reverse("activity-student", args=[activity.slug]), {"lang": code}
                )
                self.assertContains(page, f'lang="{code}"')
                self.assertContains(page, prefix + "Comments")
                self.assertContains(page, prefix + "Option A")
                self.assertNotContains(page, "Course activity")
                self.assertContains(page, 'value="1"')
                self.assertContains(page, 'name="section-records-0-category"')
                if code != "en":
                    self.assertContains(page, code + " title")
                    self.assertContains(page, code + " instructions")
                result = visitor.post(
                    reverse("activity-student", args=[activity.slug]),
                    self.answers(activity, code),
                )
                self.assertEqual(result.status_code, 302)
                if code != "en":
                    self.assertIn("lang=" + code, result.url)
                self.assertEqual(visitor.get(result.url).status_code, 200)
        self.assertEqual(Response.objects.filter(question=question).count(), 8)
        self.assertEqual(
            set(Response.objects.values_list("language", flat=True)),
            {"en", "nl", "de", "fr"},
        )
        self.assertTrue(
            all(
                r.payload["answers"]["category"] == "1"
                for r in Response.objects.filter(payload__section_id="records")
            )
        )

    def test_original_link_preview_and_results_use_the_same_renderers(self):
        activity = Activity.objects.get(slug="relationship")
        visitor = self.visitor(activity)
        url = reverse("activity-student", args=[activity.slug])
        public = visitor.get(url)
        preview_url = reverse("research-preview-activity", args=[activity.slug])
        preview = self.client.get(preview_url)
        self.assertTemplateUsed(public, "course_iframe/flexible_form.html")
        self.assertTemplateUsed(preview, "course_iframe/flexible_form.html")
        self.assertEqual(
            public.context["groups"][0]["section"],
            preview.context["groups"][0]["section"],
        )
        self.assertNotContains(public, "Course activity")
        question = activity_question(activity)
        section = preview.context["groups"][0]["section"]
        data = {
            "question_id": question.pk,
            "revision": activity.revision,
            f"section-{section['id']}-TOTAL_FORMS": 1,
            f"section-{section['id']}-INITIAL_FORMS": 0,
        }
        for field in section["fields"]:
            data[f"section-{section['id']}-0-{field['id']}"] = field["choices"][0][
                "value"
            ]
        count = Response.objects.count()
        preview_result = self.client.post(preview_url, data)
        self.assertEqual(preview_result.status_code, 200)
        self.assertTemplateUsed(preview_result, "course_iframe/flexible_results.html")
        self.assertEqual(Response.objects.count(), count)
        public_result = visitor.post(url, data, follow=True)
        self.assertTemplateUsed(public_result, "course_iframe/flexible_results.html")
        self.assertEqual(
            [c["id"] for c in preview_result.context["plot_cards"]],
            [c["id"] for c in public_result.context["plot_cards"]],
        )
        self.assertEqual(
            [c["kind"] for c in preview_result.context["plot_cards"]],
            [c["kind"] for c in public_result.context["plot_cards"]],
        )
        self.assertEqual(Response.objects.count(), count + 1)

    def test_adding_translations_preserves_question_version_and_existing_data(self):
        activity = self.create()
        old = self.save(activity, example_schema())
        visitor = self.visitor(activity)
        visitor.post(
            reverse("activity-student", args=[activity.slug]), self.answers(activity)
        )
        schema = variant(schema_for_question(old), "de")
        current = self.save(
            activity,
            schema,
            {
                "de": {
                    "title": "Deutscher Titel",
                    "description": "Deutsche Anleitung",
                    "ui": {},
                }
            },
        )
        self.assertEqual(current.pk, old.pk)
        self.assertEqual(current.version, old.version)
        self.assertEqual(activity.responses.count(), 2)
        results = self.client.get(
            reverse("research-activity-plots", args=[activity.slug]), {"lang": "de"}
        )
        self.assertEqual(results.context["response_count"], 2)
        self.assertEqual(
            next(
                c
                for c in results.context["distributions"]
                if c["field_id"] == "category"
            )["rows"][0]["label"],
            "Deutsch Option A",
        )
        self.assertEqual(
            next(
                c
                for c in results.context["distributions"]
                if c["field_id"] == "category"
            )["total"],
            1,
        )

    def test_saved_response_keeps_the_actual_language_and_wording(self):
        activity, question = self.ready()
        visitor = self.visitor(activity, "de")
        visitor.post(
            reverse("activity-student", args=[activity.slug]),
            self.answers(activity, "de"),
        )
        response = activity.responses.filter(payload__section_id="records").get()
        self.assertEqual(response.language, "de")
        before = response_answers(response)
        schema = schema_for_question(question)
        schema["sections"][1]["fields"][0]["translations"]["de"]["label"] = (
            "New German wording"
        )
        self.save(activity, schema)
        response.refresh_from_db()
        self.assertEqual(response_answers(response), before)
        csv = self.client.get(
            reverse("research-export-csv"), {"activity": activity.slug}
        )
        self.assertContains(csv, "Deutsch Category [category]")
        self.assertContains(csv, "response_language")
        self.assertEqual(activity_question(activity).pk, question.pk)

    def test_incomplete_or_unknown_languages_do_not_create_participants(self):
        activity = self.create()
        self.save(activity, example_schema(), {"de": {"title": "Titel", "ui": {}}})
        visitor = Client()
        url = reverse("activity-iframe-entry", args=[activity.slug])
        for code in ["de", "unknown"]:
            self.assertEqual(visitor.get(url, {"lang": code}).status_code, 404)
        self.assertFalse(Participant.objects.exists())
        preview = self.client.get(
            reverse("research-preview-activity", args=[activity.slug]), {"lang": "de"}
        )
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(Participant.objects.exists())

    def test_original_language_can_be_german_without_an_english_version(self):
        activity = self.create(source="de")
        self.save(activity, example_schema())
        visitor = self.visitor(activity)
        page = visitor.get(reverse("activity-student", args=[activity.slug]))
        self.assertContains(page, 'lang="de"')
        self.assertContains(page, 'aria-label="Speichern"')
        self.assertEqual(
            visitor.get(
                reverse("activity-student", args=[activity.slug]), {"lang": "en"}
            ).status_code,
            404,
        )

    def test_duplicate_translated_options_and_forged_translation_fields_are_rejected(
        self,
    ):
        activity = self.create()
        schema = variant(example_schema(), "de")
        schema["sections"][1]["fields"][0]["choices"][1]["translations"]["de"][
            "label"
        ] = schema["sections"][1]["fields"][0]["choices"][0]["translations"]["de"][
            "label"
        ]
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]),
            self.editor_data(activity, schema),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["schema_form"].errors)
        schema = example_schema()
        schema["sections"][0]["fields"][0]["translations"] = {"de": {"type": "integer"}}
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]),
            self.editor_data(activity, schema),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["schema_form"].errors)

    def test_switching_languages_reuses_existing_answers_instead_of_duplicating_them(
        self,
    ):
        activity, question = self.ready()
        visitor = self.visitor(activity, "en")
        url = reverse("activity-student", args=[activity.slug])
        visitor.post(url, self.answers(activity))
        ids = set(activity.responses.values_list("pk", flat=True))
        visitor.get(
            reverse("activity-iframe-entry", args=[activity.slug]), {"lang": "de"}
        )
        page = visitor.get(url, {"lang": "de"})
        data = {
            "revision": activity.revision,
            "question_id": question.pk,
            "language": "de",
        }
        for group in page.context["groups"]:
            for field in group["formset"].management_form:
                data[field.html_name] = field.value()
            for form in group["formset"]:
                for field in form:
                    if field.value() is not None:
                        data[field.html_name] = field.value()
        response = visitor.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("lang=de", response.url)
        self.assertEqual(set(activity.responses.values_list("pk", flat=True)), ids)
        self.assertEqual(
            set(activity.responses.values_list("language", flat=True)), {"de"}
        )
        self.assertEqual(
            activity.responses.get(payload__section_id="once").payload["answers"][
                "comments"
            ],
            "Participant text stays unchanged",
        )
        self.assertEqual(Participant.objects.count(), 1)

    def test_preview_and_students_share_chart_visibility_and_localized_button_copy(
        self,
    ):
        activity, question = self.ready()
        variants = activity.translations
        variants["de"]["ui"] = {"save": "Abschicken", "remove_entry": "Zeile löschen"}
        self.save(activity, schema_for_question(question), variants)
        visitor = self.visitor(activity, "de")
        page = visitor.get(
            reverse("activity-student", args=[activity.slug]), {"lang": "de"}
        )
        self.assertContains(page, 'aria-label="Abschicken"')
        self.assertContains(page, "Zeile löschen")
        visitor.post(
            reverse("activity-student", args=[activity.slug]),
            self.answers(activity, "de"),
        )
        chart_url = reverse("research-activity-plots", args=[activity.slug])
        charts = self.client.get(chart_url, {"lang": "de"}).context["plot_cards"]
        hidden_id = charts[0]["id"]
        update = self.client.post(
            chart_url,
            {
                "language": "de",
                "question_id": question.pk,
                "visibility_chart_id": hidden_id,
            },
        )
        self.assertIn("lang=de", update.url)
        student_results = visitor.get(
            reverse("activity-results", args=[activity.slug]), {"lang": "de"}
        )
        preview_results = self.client.get(
            reverse("research-preview-results", args=[activity.slug]), {"lang": "de"}
        )
        self.assertNotIn(
            hidden_id, {card["id"] for card in student_results.context["plot_cards"]}
        )
        self.assertEqual(
            [card["id"] for card in student_results.context["plot_cards"]],
            [card["id"] for card in preview_results.context["plot_cards"]],
        )
        invalid = self.answers(activity, "de")
        invalid["section-records-0-category"] = "forged"
        count = Response.objects.count()
        preview = self.client.post(
            reverse("research-preview-activity", args=[activity.slug]), invalid
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTemplateUsed(preview, "course_iframe/flexible_form.html")
        self.assertContains(preview, 'lang="de"')
        self.assertEqual(Response.objects.count(), count)

    def test_original_language_can_be_corrected_before_adding_versions(self):
        activity = self.create()
        data = self.editor_data(activity, example_schema())
        data["activity-source_language"] = "de"
        data["editor_language"] = "en"
        data["activity-activity_translations"] = json.dumps(
            {"en": {"ui": {"save": "Los"}}}
        )
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]), data
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("lang=", response.url)
        activity.refresh_from_db()
        self.assertEqual(activity.source_language, "de")
        self.assertEqual(activity.translations, {"de": {"ui": {"save": "Los"}}})

    def test_original_language_cannot_be_reassigned_with_existing_versions(self):
        activity, _ = self.ready()
        data = self.editor_data(activity)
        data["activity-source_language"] = "de"
        response = self.client.post(
            reverse("research-edit-activity", args=[activity.slug]), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("source_language", response.context["activity_settings"].errors)
        activity.refresh_from_db()
        self.assertEqual(activity.source_language, "en")

    def test_results_tabs_filter_every_chart_type_and_preserve_preview_answers(self):
        activity, question = self.ready()
        first, second = self.visitor(activity), self.visitor(activity)
        url = reverse("activity-student", args=[activity.slug])
        mine = self.answers(activity)
        mine["section-once-0-comments"] = "mineword"
        self.assertEqual(first.post(url, mine).status_code, 302)
        theirs = self.answers(activity)
        theirs.update(
            {
                "section-once-0-comments": "otherword",
                "section-records-0-category": "2",
                "section-records-0-rating": "1",
                "section-records-0-cost": "200",
                "section-records-0-size": "4",
            }
        )
        self.assertEqual(second.post(url, theirs).status_code, 302)
        result = first.get(reverse("activity-results", args=[activity.slug]))
        personal, aggregate = (
            result.context["personal_plot_cards"],
            result.context["plot_cards"],
        )
        self.assertEqual(
            {card["kind"] for card in personal},
            {"distribution", "numeric", "word_cloud", "scatter", "stacked"},
        )
        self.assertEqual(
            [card["id"] for card in personal], [card["id"] for card in aggregate]
        )
        for cards, count, mean in [(personal, 1, 50), (aggregate, 2, 125)]:
            for card in cards:
                data = card["data"]
                if card["kind"] == "scatter":
                    self.assertEqual(len(data["points"]), count)
                    self.assertTrue(
                        all(
                            point["is_yours"] == (count == 1)
                            for point in data["points"]
                        )
                    )
                elif card["kind"] == "stacked":
                    self.assertEqual(sum(row["total"] for row in data["rows"]), count)
                elif card["kind"] == "numeric" and data["field_id"] == "cost":
                    self.assertEqual(data["count"], count)
                    self.assertEqual(float(data["mean"]), mean)
                elif card["kind"] == "distribution" and data["field_id"] == "category":
                    self.assertEqual(data["total"], count)
                elif card["kind"] == "word_cloud" and data["field_id"] == "comments":
                    self.assertEqual(
                        {word["label"] for word in data["words"]},
                        {"mineword"} if count == 1 else {"mineword", "otherword"},
                    )
        self.assertContains(result, 'role="tablist"')
        self.assertNotContains(result, "Purple points show")
        self.assertNotContains(result, ">Edit answers</a>")
        editor = self.client.get(
            reverse("research-edit-activity", args=[activity.slug])
        )
        self.assertNotContains(editor, "Student button labels")
        self.assertNotContains(editor, ">Remove section</button>")
        before = Response.objects.count()
        preview = self.client.post(
            reverse("research-preview-activity", args=[activity.slug]), mine
        )
        self.assertEqual(Response.objects.count(), before)
        own_scatter = next(
            card
            for card in preview.context["personal_plot_cards"]
            if card["kind"] == "scatter"
        )
        all_scatter = next(
            card for card in preview.context["plot_cards"] if card["kind"] == "scatter"
        )
        self.assertEqual(len(own_scatter["data"]["points"]), 1)
        self.assertEqual(len(all_scatter["data"]["points"]), 3)
        empty = self.visitor(activity).get(
            reverse("activity-results", args=[activity.slug])
        )
        self.assertTrue(
            all(
                not card["data"].get("points")
                for card in empty.context["personal_plot_cards"]
                if card["kind"] == "scatter"
            )
        )

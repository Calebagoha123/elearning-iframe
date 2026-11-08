"""Reusable activity types shared by the original activities and new course content."""

from django import forms
from django.db import transaction
from django.urls import reverse

from .models import Activity, Question
from .localization import LANGUAGES, language_url, ui_copy, validate_activity_variants

from .services import HOUSING_QUESTION_SLUG, RELATIONSHIP_QUESTION_SLUG

ACTIVITY_TYPES = {
    "builder": {
        "label": "Activity",
        "description": "Configurable questions and repeatable sections",
    },
    "housing_survey": {
        "label": "Data entry and scatter plot",
        "description": "Like Housing data entry: students add multiple records, then compare numeric fields on a scatter plot. Includes text, numbers, choices, currency, and verified places.",
        "source": "housing",
        "question_slug": HOUSING_QUESTION_SLUG,
        "edit_template": "research/edit_housing.html",
        "student_template": "course_iframe/housing_form.html",
    },
    "relationship_survey": {
        "label": "Category and comparison survey",
        "description": "Like Relationship survey: students add categories and rate each one on a 3, 5, or 7-point scale. Results include a word cloud and stacked comparison chart.",
        "source": "relationship",
        "question_slug": RELATIONSHIP_QUESTION_SLUG,
        "edit_template": "research/edit_relationship.html",
        "student_template": "course_iframe/relationship_form.html",
    },
}


def activity_links(activity, language=None):
    language = language or activity.source_language
    links = {
        "activity": activity,
        "activity_form_url": reverse("activity-student", args=[activity.slug]),
        "activity_results_url": reverse("activity-results", args=[activity.slug]),
        "activity_edit_url": reverse("research-edit-activity", args=[activity.slug]),
        "activity_plots_url": reverse("research-activity-plots", args=[activity.slug]),
        "activity_responses_url": reverse(
            "research-activity-responses", args=[activity.slug]
        ),
        "activity_preview_url": reverse(
            "research-preview-activity", args=[activity.slug]
        ),
    }
    for key in (
        "activity_form_url",
        "activity_results_url",
        "activity_preview_url",
        "activity_plots_url",
    ):
        links[key] = language_url(links[key], language, activity.source_language)
    links.update(language=language, ui=ui_copy(activity, language))
    return links


def activity_question(activity):
    return activity.questions.get(is_active=True)


class NewActivityForm(forms.ModelForm):
    source_language = forms.ChoiceField(
        label="Original language", choices=LANGUAGES, initial="en", required=False
    )

    def clean_source_language(self):
        return self.cleaned_data.get("source_language") or "en"

    class Meta:
        model = Activity
        fields = ["title", "description", "source_language"]
        labels = {
            "description": "Course instructions",
            "source_language": "Original language",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    @transaction.atomic
    def save(self):
        from uuid import uuid4
        from django.utils.text import slugify
        from .flexible import BUILDER, SCHEMA_KEY

        activity = super().save(commit=False)
        activity.slug = (
            (slugify(activity.title)[:52] or "activity") + "-" + uuid4().hex[:12]
        )
        activity.activity_type = BUILDER
        activity.is_active = False
        activity.save()
        Question.objects.create(
            activity=activity,
            slug="activity",
            question_text=activity.title,
            field_type="activity",
            options_json={SCHEMA_KEY: {"sections": []}},
        )
        return activity


class ActivitySettingsForm(forms.ModelForm):
    source_language = forms.ChoiceField(
        label="Original language",
        choices=LANGUAGES,
        required=False,
        help_text="Set this to the language of the original text before adding other language versions.",
    )
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    activity_translations = forms.JSONField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Activity
        fields = ["title", "description", "is_active", "source_language"]
        labels = {
            "description": "Course instructions",
            "is_active": "Published — students can open this activity",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_source_language = self.instance.source_language
        self.initial["revision"] = self.instance.revision
        self.initial["activity_translations"] = self.instance.translations

    def clean_source_language(self):
        return self.cleaned_data.get("source_language") or self.original_source_language

    def clean(self):
        data = super().clean()
        variants = (
            data.get("activity_translations", self.instance.translations)
            if self.add_prefix("activity_translations") in self.data
            else self.instance.translations
        )
        if data.get(
            "source_language", self.original_source_language
        ) != self.original_source_language and any(
            code != self.original_source_language for code in variants
        ):
            self.add_error(
                "source_language",
                "Other language versions already exist. Use the editing-language selector to change their text; the original language cannot be reassigned.",
            )
        return data

    def clean_activity_translations(self):
        return validate_activity_variants(
            self.cleaned_data.get("activity_translations") or {}
        )

    def save(self, commit=True):
        activity = super().save(commit=False)
        if self.add_prefix("activity_translations") in self.data:
            activity.translations = self.cleaned_data["activity_translations"]
        if activity.source_language != self.original_source_language:
            original_ui = activity.translations.pop(
                self.original_source_language, {}
            ).get("ui", {})
            if original_ui:
                activity.translations[activity.source_language] = {"ui": original_ui}
        if commit:
            activity.save()
        return activity


class ActivityChanged(Exception):
    pass

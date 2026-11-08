"""Reusable activity types shared by the original activities and new course content."""

from django import forms
from django.db import transaction
from django.urls import reverse

from .models import Activity, Question
from .entry_pages import (
    ENTRY_LANGUAGES,
    ENTRY_COPY_FIELDS,
    ENTRY_DEFAULTS,
    entry_configuration,
)
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


def activity_links(activity):
    return {
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


def activity_question(activity):
    return activity.questions.get(is_active=True)


class NewActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["title", "description"]
        labels = {"description": "Course instructions"}
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
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)

    landing_language = forms.ChoiceField(
        label="Landing-page language", choices=ENTRY_LANGUAGES, required=False
    )
    skip_landing = forms.BooleanField(
        label="Skip landing page and assign a random participant ID", required=False
    )
    entry_heading = forms.CharField(
        label="Page heading", max_length=500, required=False
    )
    entry_intro = forms.CharField(
        label="Introduction (optional)",
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=4000,
        required=False,
    )
    entry_eyebrow = forms.CharField(
        label="Small heading (optional)", max_length=100, required=False
    )
    entry_label = forms.CharField(
        label="Participant ID label", max_length=200, required=False
    )
    entry_help = forms.CharField(
        label="Participant ID hint (optional)", max_length=1000, required=False
    )
    entry_privacy = forms.CharField(
        label="Research/privacy notice (optional)",
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=6000,
        required=False,
    )
    entry_button = forms.CharField(
        label="Start button text", max_length=100, required=False
    )

    class Meta:
        model = Activity
        fields = ["title", "description", "is_active"]
        labels = {
            "description": "Course instructions",
            "is_active": "Published — students can open this activity",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["revision"] = self.instance.revision
        copy = entry_configuration(self.instance)
        self.initial["landing_language"] = copy["language"]
        self.initial["skip_landing"] = copy["skip"]
        for key in ENTRY_COPY_FIELDS:
            self.initial["entry_" + key] = copy[key]
        self.initial["entry_heading"] = self.instance.entry_settings.get("heading", "")
        self.fields["entry_heading"].widget.attrs["placeholder"] = (
            "Use the activity title"
        )

    def entry_presets(self):
        return ENTRY_DEFAULTS

    def detail_fields(self):
        return [self[name] for name in ("title", "description", "is_active")]

    def landing_fields(self):
        return [
            self[name]
            for name in (
                "landing_language",
                "skip_landing",
                *("entry_" + key for key in ENTRY_COPY_FIELDS),
            )
        ]

    def save(self, commit=True):
        activity = super().save(commit=False)
        copy = entry_configuration(activity)
        language = self.cleaned_data.get("landing_language") or copy["language"]
        full_entry_form = self.add_prefix("landing_language") in self.data
        settings = {
            "language": language,
            "skip": self.cleaned_data.get("skip_landing", False)
            if full_entry_form
            else copy["skip"],
        }
        for key in ENTRY_COPY_FIELDS:
            name = "entry_" + key
            # Older clients posting activity metadata retain their existing copy.
            settings[key] = (
                self.cleaned_data.get(name, "")
                if self.add_prefix(name) in self.data
                else copy[key]
            )
            if (
                language != copy["language"]
                and key in ENTRY_DEFAULTS[copy["language"]]
                and settings[key] == ENTRY_DEFAULTS[copy["language"]][key]
            ):
                settings[key] = ENTRY_DEFAULTS[language][key]
        for key in ("label", "button"):
            settings[key] = settings[key] or ENTRY_DEFAULTS[language][key]
        activity.entry_settings = settings
        if commit:
            activity.save()
        return activity


class ActivityChanged(Exception):
    pass

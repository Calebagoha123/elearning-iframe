"""Reusable activity types shared by the original activities and new course content."""

from django import forms
from django.db import transaction
from django.urls import reverse

from .models import Activity, Question
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


class ActivityChanged(Exception):
    pass

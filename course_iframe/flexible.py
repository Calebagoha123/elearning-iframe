"""Versioned activity schemas: any mix of questions in single or repeatable sections."""

from copy import deepcopy
from decimal import Decimal
import re
from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import BaseFormSet, formset_factory

from .models import Activity, Question, Response
from .services import housing_schema, relationship_likert_choices
from .nominatim import verify_place, PlaceVerificationError

BUILDER = "builder"
SCHEMA_KEY = "activity_schema"
FIELD_TYPES = [
    ("text", "Short text"),
    ("long_text", "Long text"),
    ("choice", "Single choice"),
    ("multiple", "Multiple choice"),
    ("likert", "Likert scale"),
    ("integer", "Whole number"),
    ("decimal", "Decimal number"),
    ("currency_eur", "Currency (€)"),
    ("place", "Location"),
]
NUMERIC_TYPES = {"integer", "decimal", "currency_eur"}
NUMERIC_LIMITS = {
    "integer": Decimal("1000000000000000"),
    "decimal": Decimal("99999999999999.999999"),
    "currency_eur": Decimal("999999999999.99"),
}
CHOICE_TYPES = {"choice", "multiple", "likert"}
MAX_ENTRIES = 1000


def new_id():
    return uuid4().hex[:16]


def current_question(activity):
    return activity.questions.get(is_active=True)


def schema_for_question(question):
    if SCHEMA_KEY in question.options_json:
        schema = deepcopy(question.options_json[SCHEMA_KEY])
        for section in schema["sections"]:
            for field in section["fields"]:
                field.setdefault("version", 1)
        return schema
    if (
        question.activity.activity_type == "housing_survey"
        or question.field_type == "housing"
    ):
        fields = []
        for spec in housing_schema(question):
            fields.append(
                {
                    "id": spec["key"],
                    "version": 1,
                    "label": spec["label"],
                    "type": spec["type"],
                    "required": spec.get("required", True),
                    "help_text": spec.get("help_text", ""),
                    "choices": deepcopy(spec.get("options", [])),
                    "allow_other": False,
                    "min_value": "0" if spec["type"] in NUMERIC_TYPES else "",
                }
            )
        return {
            "sections": [
                {
                    "id": "entries",
                    "title": "Entries",
                    "instructions": question.question_text,
                    "repeatable": True,
                    "add_label": "Add another entry",
                    "fields": fields,
                }
            ]
        }
    fields = [
        {
            "id": "relationship_value",
            "version": 1,
            "label": question.options_json.get("item_label", "Relationship"),
            "type": "choice",
            "required": True,
            "help_text": "",
            "choices": deepcopy(question.options_json.get("choices", [])),
            "allow_other": True,
            "min_value": "",
        },
        {
            "id": "income_comparison_value",
            "version": 1,
            "label": question.options_json.get("comparison_label", "Your income"),
            "type": "likert",
            "required": True,
            "help_text": "",
            "choices": [
                {"value": value, "label": label}
                for value, label in relationship_likert_choices(question)
            ],
            "allow_other": False,
            "min_value": "",
        },
    ]
    return {
        "sections": [
            {
                "id": "relationships",
                "title": "Responses",
                "instructions": question.question_text,
                "repeatable": True,
                "add_label": "Add another response",
                "fields": fields,
            }
        ]
    }


def validate_schema(raw, published=False):
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        raise ValidationError("Add sections and questions using the activity builder.")
    if len(raw["sections"]) > 30:
        raise ValidationError("An activity can contain up to 30 sections.")
    ids, sections = set(), []
    field_count = 0

    def identifier(value):
        value = value or new_id()
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", value)
            or value in ids
            or value == "response_id"
            or "__" in value
        ):
            raise ValidationError(
                "Question or section identifiers are duplicated or invalid. Reload the editor."
            )
        ids.add(value)
        return value

    def text(value, limit):
        if not isinstance(value, str) or len(value) > limit:
            raise ValidationError(
                f"Keep labels and instructions within {limit} characters."
            )
        return value.strip()

    for index, section in enumerate(raw["sections"]):
        if not isinstance(section, dict) or not isinstance(section.get("fields"), list):
            raise ValidationError("A section must contain a list of questions.")
        group = {
            "id": identifier(section.get("id")),
            "title": text(section.get("title", ""), 160),
            "instructions": text(section.get("instructions", ""), 4000),
            "repeatable": bool(section.get("repeatable", False)),
            "add_label": text(section.get("add_label", "Add another entry"), 100)
            or "Add another entry",
            "fields": [],
        }
        for field in section["fields"]:
            if not isinstance(field, dict):
                raise ValidationError("Question settings are invalid.")
            label = text(field.get("label", ""), 500)
            if not label:
                raise ValidationError(
                    f"Section {index + 1}: give every question a label, or remove the empty question."
                )
            kind = field.get("type")
            if not isinstance(kind, str) or kind not in dict(FIELD_TYPES):
                raise ValidationError(f"{label}: choose a supported answer type.")
            spec = {
                "id": identifier(field.get("id")),
                "version": field.get("version", 1)
                if type(field.get("version", 1)) is int and field.get("version", 1) > 0
                else 1,
                "label": label,
                "type": kind,
                "required": bool(field.get("required", True)),
                "help_text": text(field.get("help_text", ""), 1000),
                "allow_other": bool(field.get("allow_other", False))
                if kind == "choice"
                else False,
                "choices": [],
                "min_value": "",
            }
            if kind in CHOICE_TYPES:
                choices = field.get("choices", [])
                if not isinstance(choices, list) or not (
                    3 if kind == "likert" else 2
                ) <= len(choices) <= (11 if kind == "likert" else 200):
                    raise ValidationError(
                        f"{label}: add enough answer options (at least 3 for Likert, 2 for choices; up to {'11' if kind == 'likert' else '200'})."
                    )
                values, labels = set(), set()
                for choice in choices:
                    if not isinstance(choice, dict):
                        raise ValidationError(f"{label}: answer options are invalid.")
                    value = text(choice.get("value", ""), 200)
                    option = text(choice.get("label", ""), 200)
                    if (
                        not value
                        or not option
                        or value in values
                        or option.casefold() in labels
                        or value == "__other__"
                    ):
                        raise ValidationError(
                            f"{label}: each answer option must be non-empty and different."
                        )
                    values.add(value)
                    labels.add(option.casefold())
                    spec["choices"].append({"value": value, "label": option})
            if kind in NUMERIC_TYPES and field.get("min_value") not in (None, ""):
                try:
                    minimum = Decimal(str(field["min_value"]))
                    if (
                        not minimum.is_finite()
                        or abs(minimum) > NUMERIC_LIMITS[kind]
                        or (
                            kind == "integer" and minimum != minimum.to_integral_value()
                        )
                    ):
                        raise ValueError
                except Exception:
                    raise ValidationError(f"{label}: enter a valid minimum number.")
                spec["min_value"] = str(minimum)
            group["fields"].append(spec)
            field_count += 1
        if not group["fields"]:
            raise ValidationError(
                f"Section {index + 1}: add a question, or remove this section."
            )
        sections.append(group)
    if field_count > 200:
        raise ValidationError("An activity can contain up to 200 questions.")
    if published and not sections:
        raise ValidationError("Add at least one question before publishing.")
    return {"sections": sections}


def version_questions(previous, incoming):
    """Only the questions whose definitions change get a new question version."""
    old = {
        field["id"]: field
        for section in previous["sections"]
        for field in section["fields"]
    }
    result = deepcopy(incoming)
    for section in result["sections"]:
        for field in section["fields"]:
            before = old.get(field["id"])
            definition = {
                key: value for key, value in field.items() if key != "version"
            }
            old_definition = (
                {key: value for key, value in before.items() if key != "version"}
                if before
                else None
            )
            field["version"] = (
                (before.get("version", 1) + int(definition != old_definition))
                if before
                else 1
            )
    return result


class SchemaEditorForm(forms.Form):
    schema = forms.JSONField(widget=forms.HiddenInput)

    def __init__(self, *args, published=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.published = published

    def clean_schema(self):
        try:
            return validate_schema(self.cleaned_data["schema"], self.published)
        except ValidationError:
            raise


class SectionResponseForm(forms.Form):
    response_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, section, **kwargs):
        super().__init__(*args, **kwargs)
        self.section = section
        for spec in section["fields"]:
            key, kind = spec["id"], spec["type"]
            common = dict(
                label=spec["label"],
                required=spec.get("required", True),
                help_text=spec.get("help_text", ""),
            )
            choices = [(c["value"], c["label"]) for c in spec.get("choices", [])]
            if kind in ("choice", "likert"):
                if spec.get("allow_other"):
                    choices.append(("__other__", "Other"))
                    self.fields[key + "__other"] = forms.CharField(
                        label="Other answer", required=False, max_length=200
                    )
                field = forms.ChoiceField(
                    choices=choices
                    if kind == "likert"
                    else [("", "Choose an answer")] + choices,
                    widget=forms.RadioSelect if kind == "likert" else forms.Select,
                    **common,
                )
            elif kind == "multiple":
                field = forms.MultipleChoiceField(
                    choices=choices, widget=forms.CheckboxSelectMultiple, **common
                )
            elif kind in NUMERIC_TYPES:
                minimum = (
                    Decimal(spec["min_value"])
                    if spec.get("min_value") not in (None, "")
                    else None
                )
                field = (
                    forms.IntegerField(min_value=minimum, max_value=10**15, **common)
                    if kind == "integer"
                    else forms.DecimalField(
                        max_digits=14 if kind == "currency_eur" else 20,
                        decimal_places=2 if kind == "currency_eur" else 6,
                        min_value=minimum,
                        **common,
                    )
                )
            else:
                field = forms.CharField(
                    max_length=10000 if kind == "long_text" else 1000,
                    widget=forms.Textarea(attrs={"rows": 3})
                    if kind == "long_text"
                    else forms.TextInput,
                    **common,
                )
            self.fields[key] = field
            if kind == "place":
                field.widget.attrs["data-place-input"] = ""
                for suffix in ["osm_type", "osm_id", "display_name"]:
                    self.fields[f"{key}__{suffix}"] = forms.CharField(
                        required=False, widget=forms.HiddenInput
                    )

    def clean(self):
        data = super().clean()
        for spec in self.section["fields"]:
            key = spec["id"]
            if (
                spec.get("allow_other")
                and data.get(key) == "__other__"
                and not data.get(key + "__other")
            ):
                self.add_error(key + "__other", "Describe your other answer.")
            if spec["type"] == "place" and data.get(key):
                try:
                    verified = verify_place(
                        data.get(key + "__osm_type", ""), data.get(key + "__osm_id", "")
                    )
                    data[key + "__place_meta"] = verified
                except (PlaceVerificationError, ValueError):
                    self.add_error(
                        key, "Pick a verified place from the dropdown suggestions."
                    )
        return data

    def field_rows(self):
        rows = []
        for spec in self.section["fields"]:
            key = spec["id"]
            rows.append(
                {
                    "spec": spec,
                    "field": self[key],
                    "other": self[key + "__other"] if spec.get("allow_other") else None,
                    "place_fields": [
                        self[key + "__" + suffix]
                        for suffix in ["osm_type", "osm_id", "display_name"]
                    ]
                    if spec["type"] == "place"
                    else [],
                }
            )
        return rows


class SectionFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ids = [
            f.cleaned_data.get("response_id")
            for f in self.forms
            if f.cleaned_data.get("response_id")
        ]
        if len(ids) != len(set(ids)):
            raise ValidationError(
                "The same entry was submitted twice. Reload the activity."
            )


def decoded_entry(response):
    """Read original response payloads as well as entries created by the builder."""
    if "section_id" in response.payload and "answers" in response.payload:
        return response.payload["section_id"], response.payload["answers"]
    schema = schema_for_question(response.question)
    section = schema["sections"][0]
    answers = {f["id"]: response.payload.get(f["id"], "") for f in section["fields"]}
    for spec in section["fields"]:
        meta = response.payload.get(spec["id"] + "_meta")
        if spec["type"] == "place" and isinstance(meta, dict) and meta:
            answers[spec["id"]] = {
                **meta,
                "display_name": meta.get("display_name") or answers[spec["id"]],
            }
    if (
        section["id"] == "relationships"
        and response.payload.get("relationship_value") == "other"
    ):
        answers["relationship_value"] = {
            "value": "__other__",
            "other": response.payload.get("relationship_label", "Other"),
        }
    return section["id"], answers


def section_formsets(question, participant=None, data=None):
    schema = schema_for_question(question)
    initial = {}
    if participant:
        for response in Response.objects.filter(
            participant=participant, question=question
        ).order_by("created_at", "pk"):
            section_id, answers = decoded_entry(response)
            row = {"response_id": response.pk}
            for key, value in answers.items():
                if isinstance(value, dict) and "value" in value:
                    row[key], row[key + "__other"] = (
                        value["value"],
                        value.get("other", ""),
                    )
                elif isinstance(value, dict) and "display_name" in value:
                    row[key] = value["display_name"]
                    for suffix in ["osm_type", "osm_id", "display_name"]:
                        row[key + "__" + suffix] = value.get(suffix, "")
                else:
                    row[key] = value
            initial.setdefault(section_id, []).append(row)
    groups = []
    for section in schema["sections"]:
        existing_rows = initial.get(section["id"], [])
        requires_row = not section["repeatable"] and any(
            field.get("required", True) for field in section["fields"]
        )
        factory = formset_factory(
            SectionResponseForm,
            formset=SectionFormSet,
            extra=1 if not existing_rows and not requires_row else 0,
            min_num=1 if requires_row else 0,
            validate_min=requires_row,
            max_num=MAX_ENTRIES if section["repeatable"] else 1,
            validate_max=True,
            absolute_max=MAX_ENTRIES,
        )
        formset = factory(
            data,
            initial=existing_rows,
            prefix="section-" + section["id"],
            form_kwargs={"section": section},
        )
        groups.append({"section": section, "formset": formset})
    return groups


def encoded_answers(form):
    answers = {}
    for spec in form.section["fields"]:
        key = spec["id"]
        value = form.cleaned_data.get(key)
        if isinstance(value, Decimal):
            value = str(value)
        if spec.get("allow_other") and value == "__other__":
            value = {"value": "__other__", "other": form.cleaned_data[key + "__other"]}
        if spec["type"] == "place" and value:
            meta = form.cleaned_data.get(key + "__place_meta") or {}
            value = {
                **{k: str(v) for k, v in meta.items()},
                "display_name": meta.get("display_name") or value,
            }
        answers[key] = value
    return answers


@transaction.atomic
def save_entries(activity, participant, question, groups, revision):
    current = Activity.objects.select_for_update().get(pk=activity.pk)
    if (
        current.deleted_at is not None
        or current.revision != revision
        or not current.is_active
        or not Question.objects.filter(pk=question.pk, is_active=True).exists()
    ):
        raise ValidationError(
            "This activity has changed. Reload it before saving your answers."
        )
    existing = {
        r.pk: r
        for r in Response.objects.filter(
            participant=participant, activity=activity, question=question
        )
    }
    retained, seen = set(), set()
    for group in groups:
        section = group["section"]
        for form in group["formset"]:
            key = form.cleaned_data.get("response_id")
            if not form.cleaned_data:
                continue
            payload = {"section_id": section["id"], "answers": encoded_answers(form)}
            if key:
                if (
                    key in seen
                    or key not in existing
                    or decoded_entry(existing[key])[0] != section["id"]
                ):
                    raise ValidationError(
                        "An entry changed or does not belong to you. Reload the activity before saving."
                    )
                response = existing[key]
                response.payload = payload
                response.save(update_fields=["payload", "updated_at"])
                seen.add(key)
            else:
                response = Response.objects.create(
                    participant=participant,
                    activity=activity,
                    question=question,
                    question_version=question.version,
                    payload=payload,
                )
            retained.add(response.pk)
    Response.objects.filter(participant=participant, question=question).exclude(
        pk__in=retained
    ).delete()


def answer_label(spec, value):
    if value is None or value == "":
        return "—"
    if isinstance(value, dict):
        return value.get("other") or value.get("display_name") or str(value)
    labels = {c["value"]: c["label"] for c in spec.get("choices", [])}
    if isinstance(value, list):
        return ", ".join(labels.get(v, str(v)) for v in value) or "—"
    return labels.get(str(value), str(value))


def response_answers(response):
    section_id, answers = decoded_entry(response)
    schema = schema_for_question(response.question)
    section = next((s for s in schema["sections"] if s["id"] == section_id), None)
    return (
        [
            {
                "label": f["label"],
                "value": answer_label(f, answers.get(f["id"])),
                "version": f.get("version", 1),
            }
            for f in section["fields"]
        ]
        if section
        else []
    )


def export_payload(response=None, question=None):
    """Readable CSV headings retain stable field keys and historical wording."""
    question = response.question if response else question
    schema = schema_for_question(question)
    if response:
        section_id, answers = decoded_entry(response)
    else:
        section_id, answers = "", {}
    output = {"section_id": section_id}
    for section in schema["sections"]:
        for spec in section["fields"]:
            heading = f"{spec['label']} [{spec['id']}]"
            value = answers.get(spec["id"], "") if section["id"] == section_id else ""
            if isinstance(value, (dict, list)):
                value = answer_label(spec, value)
            output[heading] = value
    return output

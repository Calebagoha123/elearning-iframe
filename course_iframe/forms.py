from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils.text import slugify

from .nominatim import PlaceVerificationError, verify_place


class ParticipantEntryForm(forms.Form):
    pseudoname = forms.CharField(
        label="Pseudoname",
        max_length=160,
        help_text="Use the pseudoname shown in Moodle.",
        error_messages={"required": "Enter your Moodle pseudoname."},
    )

    def clean_pseudoname(self):
        pseudoname = self.cleaned_data["pseudoname"].strip()
        if not pseudoname:
            raise forms.ValidationError("Enter your Moodle pseudoname.")
        return pseudoname


class RelationshipForm(forms.Form):
    relationship = forms.ChoiceField(label="Relationship", required=False)
    income_comparison = forms.ChoiceField(
        label="Your income",
        required=False,
        widget=forms.RadioSelect,
    )
    other_relationship = forms.CharField(
        label="Other relationship",
        max_length=120,
        required=False,
    )

    def __init__(self, *args, choices=None, income_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        base_choices = [(choice["value"], choice["label"]) for choice in choices or []]
        self.fields["relationship"].choices = [("", "Select a relationship")] + base_choices + [("other", "Other")]
        self.fields["income_comparison"].choices = list(income_choices or [])
        self.fields["relationship"].widget.attrs["data-relationship-select"] = ""
        self.fields["other_relationship"].widget.attrs["placeholder"] = "Describe the relationship"

    def clean(self):
        cleaned = super().clean()
        relationship = cleaned.get("relationship")
        income_comparison = cleaned.get("income_comparison")
        other = (cleaned.get("other_relationship") or "").strip()
        if other and not relationship:
            self.add_error("relationship", "Select Other before entering a custom relationship.")
        if income_comparison and not relationship:
            self.add_error("relationship", "Select a relationship before comparing income.")
        if relationship == "other" and not other:
            self.add_error("other_relationship", "Enter the relationship type.")
        if relationship and not income_comparison:
            self.add_error("income_comparison", "Select whether their income is lower, the same, or higher.")
        return cleaned

    def relationship_label(self):
        relationship = self.cleaned_data["relationship"]
        if relationship == "other":
            return self.cleaned_data["other_relationship"].strip()
        return dict(self.fields["relationship"].choices).get(relationship, relationship)

    def income_comparison_label(self):
        income_comparison = self.cleaned_data["income_comparison"]
        return dict(self.fields["income_comparison"].choices).get(income_comparison, income_comparison)


class BaseRelationshipFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        completed_forms = [
            form
            for form in self.forms
            if form.cleaned_data.get("relationship")
        ]
        if not completed_forms:
            raise forms.ValidationError("Add at least one relationship.")

    def relationship_payloads(self):
        payloads = []
        for form in self.forms:
            relationship = form.cleaned_data.get("relationship")
            if not relationship:
                continue
            payloads.append(
                {
                    "relationship_value": relationship,
                    "relationship_label": form.relationship_label(),
                    "income_comparison_value": form.cleaned_data["income_comparison"],
                    "income_comparison_label": form.income_comparison_label(),
                }
            )
        return payloads


RelationshipFormSet = formset_factory(
    RelationshipForm,
    formset=BaseRelationshipFormSet,
    extra=1,
    max_num=20,
    validate_max=True,
)


class HousingEntryForm(forms.Form):
    """Dynamic housing entry form built from a question schema.

    Each field in ``question.options_json["fields"]`` is rendered as a strictly
    typed Django form field. ``place`` fields are accompanied by hidden inputs
    that carry the OpenStreetMap identifiers picked from the autocomplete; they
    are verified against Nominatim on submit.
    """

    def __init__(self, *args, question=None, **kwargs):
        from .services import (
            HOUSING_FIELD_CHOICE,
            HOUSING_FIELD_CURRENCY_EUR,
            HOUSING_FIELD_DECIMAL,
            HOUSING_FIELD_INTEGER,
            HOUSING_FIELD_PLACE,
            HOUSING_FIELD_TEXT,
            housing_schema,
        )

        super().__init__(*args, **kwargs)
        self.question = question
        self._schema = housing_schema(question) if question is not None else []
        self.place_field_keys = []

        for spec in self._schema:
            key = spec["key"]
            label = spec.get("label") or key
            required = bool(spec.get("required"))
            help_text = spec.get("help_text", "")
            ftype = spec.get("type")

            if ftype == HOUSING_FIELD_CURRENCY_EUR:
                field = forms.DecimalField(
                    label=label,
                    required=required,
                    min_value=Decimal("0"),
                    max_digits=12,
                    decimal_places=2,
                    help_text=help_text,
                )
                field.widget.attrs.update({
                    "inputmode": "decimal",
                    "step": "0.01",
                    "min": "0",
                    "data-currency": "eur",
                    "placeholder": "0.00",
                })
            elif ftype == HOUSING_FIELD_DECIMAL:
                field = forms.DecimalField(
                    label=label,
                    required=required,
                    min_value=Decimal("0"),
                    max_digits=10,
                    decimal_places=2,
                    help_text=help_text,
                )
                field.widget.attrs.update({"inputmode": "decimal", "step": "0.01", "min": "0"})
            elif ftype == HOUSING_FIELD_INTEGER:
                field = forms.IntegerField(
                    label=label,
                    required=required,
                    min_value=0,
                    help_text=help_text,
                )
                field.widget.attrs.update({"inputmode": "numeric", "step": "1", "min": "0"})
            elif ftype == HOUSING_FIELD_PLACE:
                field = forms.CharField(
                    label=label,
                    required=required,
                    max_length=240,
                    help_text=help_text or "Pick a location from the suggestions.",
                )
                field.widget.attrs.update({
                    "data-place-input": key,
                    "autocomplete": "off",
                    "placeholder": "Start typing a location…",
                })
                self.fields[f"{key}_osm_type"] = forms.CharField(
                    required=False,
                    widget=forms.HiddenInput(attrs={"data-place-osm-type": key}),
                )
                self.fields[f"{key}_osm_id"] = forms.CharField(
                    required=False,
                    widget=forms.HiddenInput(attrs={"data-place-osm-id": key}),
                )
                self.fields[f"{key}_display_name"] = forms.CharField(
                    required=False,
                    widget=forms.HiddenInput(attrs={"data-place-display": key}),
                )
                self.place_field_keys.append(key)
            elif ftype == HOUSING_FIELD_CHOICE:
                options = spec.get("options") or []
                choices = [("", "Select…")] + [
                    (option.get("value", option.get("label", "")), option.get("label", ""))
                    for option in options
                    if option.get("label")
                ]
                field = forms.ChoiceField(
                    label=label,
                    required=required,
                    choices=choices,
                    help_text=help_text,
                )
            else:  # text
                field = forms.CharField(
                    label=label,
                    required=required,
                    max_length=240,
                    help_text=help_text,
                )
            self.fields[key] = field

    def schema(self):
        return list(self._schema)

    def visible_field_keys(self):
        return [field["key"] for field in self._schema]

    def visible_field(self, key):
        return self[key]

    def field_meta(self, key):
        for spec in self._schema:
            if spec["key"] == key:
                return spec
        return None

    def field_rows(self):
        rows = []
        for spec in self._schema:
            key = spec["key"]
            row = {"spec": spec, "field": self[key]}
            if spec["type"] == "place":
                row["osm_type_field"] = self[f"{key}_osm_type"]
                row["osm_id_field"] = self[f"{key}_osm_id"]
                row["display_field"] = self[f"{key}_display_name"]
            rows.append(row)
        return rows

    def clean(self):
        cleaned = super().clean()
        for key in self.place_field_keys:
            spec = self.field_meta(key) or {}
            required = bool(spec.get("required"))
            display = (cleaned.get(key) or "").strip()
            osm_type = (cleaned.get(f"{key}_osm_type") or "").strip()
            osm_id = (cleaned.get(f"{key}_osm_id") or "").strip()
            hidden_display = (cleaned.get(f"{key}_display_name") or "").strip()

            if not display and not osm_id:
                if required:
                    self.add_error(key, "Pick a location from the suggestions.")
                cleaned[f"{key}_meta"] = {}
                continue

            if not (osm_type and osm_id):
                self.add_error(key, "Choose a location from the dropdown suggestions.")
                continue

            try:
                meta = verify_place(osm_type, osm_id)
            except PlaceVerificationError as exc:
                self.add_error(key, str(exc))
                continue

            verified_display = meta.get("display_name") or hidden_display or display
            cleaned[key] = verified_display
            cleaned[f"{key}_meta"] = {
                "display_name": verified_display,
                "osm_type": meta["osm_type"],
                "osm_id": meta["osm_id"],
                "lat": meta.get("lat", ""),
                "lon": meta.get("lon", ""),
            }
        return cleaned


class ResearchLoginForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)


class RelationshipSettingsForm(forms.Form):
    income_likert_points = forms.ChoiceField(
        label="Income scale",
        choices=[
            (3, "3 point"),
            (5, "5 point"),
            (7, "7 point"),
        ],
    )


class RelationshipEditForm(forms.Form):
    question_text = forms.CharField(
        label="Question wording",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    relationship_choices = forms.CharField(
        label="Relationship options",
        help_text="Enter one option per line. Other is added automatically.",
        widget=forms.Textarea(attrs={"rows": 8}),
    )
    likert_points = forms.ChoiceField(
        label="Income scale",
        choices=[
            ("3", "3 point"),
            ("5", "5 point"),
            ("7", "7 point"),
        ],
    )

    def __init__(self, *args, question=None, **kwargs):
        from .services import LIKERT_CHOICES, relationship_likert_points
        super().__init__(*args, **kwargs)
        self.question = question
        self._likert_definitions = LIKERT_CHOICES

        saved_labels = {}
        self._existing_choice_values = {}
        if question is not None:
            options = question.options_json or {}
            saved_labels = options.get("likert_labels") or {}
            self._existing_choice_values = {
                (choice.get("label") or "").strip().lower(): choice.get("value", "")
                for choice in options.get("choices", [])
                if (choice.get("label") or "").strip()
            }
            if not self.is_bound:
                self.fields["question_text"].initial = question.question_text
                self.fields["relationship_choices"].initial = "\n".join(
                    choice.get("label", "")
                    for choice in options.get("choices", [])
                    if choice.get("label")
                )
                self.fields["likert_points"].initial = str(relationship_likert_points(question))

        for points, choices in LIKERT_CHOICES.items():
            scale_overrides = saved_labels.get(str(points), {})
            for key, default_label in choices:
                field_name = self.label_field_name(points, key)
                initial = scale_overrides.get(key, default_label) if not self.is_bound else None
                self.fields[field_name] = forms.CharField(
                    label=default_label,
                    initial=initial,
                    max_length=120,
                    required=False,
                )

    @staticmethod
    def label_field_name(points, key):
        return f"label_{points}_{key}"

    def clean(self):
        cleaned = super().clean()
        points = cleaned.get("likert_points")
        if not points:
            return cleaned
        try:
            points_int = int(points)
        except (TypeError, ValueError):
            self.add_error("likert_points", "Choose a valid scale.")
            return cleaned
        if points_int not in self._likert_definitions:
            self.add_error("likert_points", "Choose a valid scale.")
            return cleaned
        for key, default_label in self._likert_definitions[points_int]:
            field_name = self.label_field_name(points_int, key)
            value = (cleaned.get(field_name) or "").strip()
            if not value:
                self.add_error(field_name, f"Provide a label for '{default_label}'.")
            else:
                cleaned[field_name] = value
        return cleaned

    def clean_relationship_choices(self):
        raw = self.cleaned_data["relationship_choices"]
        labels = [line.strip() for line in raw.splitlines() if line.strip()]
        if not labels:
            raise forms.ValidationError("Add at least one relationship option.")

        choices = []
        seen_labels = set()
        seen_values = set()
        for label in labels:
            label_key = label.lower()
            if label_key == "other":
                raise forms.ValidationError("Other is added automatically; remove it from this list.")
            if label_key in seen_labels:
                raise forms.ValidationError(f"'{label}' appears more than once.")
            seen_labels.add(label_key)

            value = self._existing_choice_values.get(label_key) or slugify(label).replace("-", "_")
            if not value:
                value = "relationship"
            base_value = value
            suffix = 2
            while value in seen_values or value == "other":
                value = f"{base_value}_{suffix}"
                suffix += 1
            seen_values.add(value)
            choices.append({"value": value, "label": label})
        return choices

    def labels_for_selected_scale(self):
        points = int(self.cleaned_data["likert_points"])
        return {
            key: self.cleaned_data[self.label_field_name(points, key)]
            for key, _ in self._likert_definitions[points]
        }

    def relationship_choices_for_save(self):
        return self.cleaned_data["relationship_choices"]

    def has_meaningful_changes(self):
        from .services import relationship_likert_points

        if self.question is None:
            return True
        options = self.question.options_json or {}
        if self.cleaned_data["question_text"] != self.question.question_text:
            return True
        if self.relationship_choices_for_save() != list(options.get("choices", [])):
            return True
        selected_points = int(self.cleaned_data["likert_points"])
        if selected_points != relationship_likert_points(self.question):
            return True

        current_labels = {}
        saved_labels = (options.get("likert_labels") or {}).get(str(selected_points), {})
        for key, default_label in self._likert_definitions[selected_points]:
            current_labels[key] = saved_labels.get(key, default_label)
        return self.labels_for_selected_scale() != current_labels

    def label_field_groups(self):
        groups = []
        for points, choices in self._likert_definitions.items():
            fields = []
            for key, default_label in choices:
                fields.append(
                    {
                        "key": key,
                        "default_label": default_label,
                        "field": self[self.label_field_name(points, key)],
                    }
                )
            groups.append({"points": points, "fields": fields})
        return groups


class HousingFieldForm(forms.Form):
    """A single field row in the researcher's housing schema editor."""

    key = forms.CharField(required=False, widget=forms.HiddenInput())
    label = forms.CharField(max_length=120)
    field_type = forms.ChoiceField()
    required = forms.BooleanField(required=False)
    help_text = forms.CharField(max_length=240, required=False)
    options_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "data-field-options": ""}),
        help_text="One option per line. Only used for the Single choice type.",
    )
    delete = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        from .services import HOUSING_FIELD_TYPE_CHOICES
        super().__init__(*args, **kwargs)
        self.fields["field_type"].choices = HOUSING_FIELD_TYPE_CHOICES

    def clean_label(self):
        return self.cleaned_data["label"].strip()

    def clean_options_text(self):
        raw = self.cleaned_data.get("options_text") or ""
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def clean(self):
        from .services import HOUSING_FIELD_CHOICE
        cleaned = super().clean()
        if cleaned.get("delete"):
            return cleaned
        label = cleaned.get("label", "")
        ftype = cleaned.get("field_type")
        if not label:
            self.add_error("label", "Field label is required.")
        if ftype == HOUSING_FIELD_CHOICE and not cleaned.get("options_text"):
            self.add_error("options_text", "Add at least one option for a single choice field.")
        return cleaned


class HousingFieldFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen_labels = set()
        seen_keys = set()
        kept = 0
        for form in self.forms:
            if not form.has_changed() and not form.cleaned_data:
                continue
            if form.cleaned_data.get("delete"):
                continue
            label_key = form.cleaned_data.get("label", "").lower()
            if not label_key:
                continue
            kept += 1
            if label_key in seen_labels:
                raise forms.ValidationError(
                    f"'{form.cleaned_data['label']}' appears more than once."
                )
            seen_labels.add(label_key)
            existing_key = (form.cleaned_data.get("key") or "").strip()
            if existing_key:
                if existing_key in seen_keys:
                    raise forms.ValidationError("Duplicate field key detected.")
                seen_keys.add(existing_key)
        if kept == 0:
            raise forms.ValidationError("Add at least one housing field.")


def _slugify_field_key(label, existing):
    base = slugify(label).replace("-", "_") or "field"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


HousingFieldFormSetFactory = formset_factory(
    HousingFieldForm,
    formset=HousingFieldFormSet,
    extra=0,
    can_order=True,
    can_delete=False,
)


class HousingEditForm(forms.Form):
    """Researcher editor for the housing question wording, schema, and scatter axes."""

    question_text = forms.CharField(
        label="Question wording",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    x_axis = forms.ChoiceField(label="Scatter X-axis")
    y_axis = forms.ChoiceField(label="Scatter Y-axis")

    def __init__(self, *args, question=None, **kwargs):
        from .services import housing_axis_choices, housing_scatter_config, housing_schema
        super().__init__(*args, **kwargs)
        self.question = question
        self._axis_choices = housing_axis_choices(question) if question is not None else []
        self.fields["x_axis"].choices = self._axis_choices
        self.fields["y_axis"].choices = self._axis_choices
        if question is not None and not self.is_bound:
            config = housing_scatter_config(question)
            self.fields["question_text"].initial = question.question_text
            self.fields["x_axis"].initial = config["x_axis"]
            self.fields["y_axis"].initial = config["y_axis"]
        self._initial_schema = housing_schema(question) if question is not None else []

    def initial_field_rows(self):
        rows = []
        for field in self._initial_schema:
            rows.append(
                {
                    "key": field.get("key", ""),
                    "label": field.get("label", ""),
                    "field_type": field.get("type", ""),
                    "required": bool(field.get("required")),
                    "help_text": field.get("help_text", ""),
                    "options_text": "\n".join(
                        option.get("label", "") for option in (field.get("options") or []) if option.get("label")
                    ),
                }
            )
        return rows

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("x_axis") and cleaned.get("y_axis") and cleaned["x_axis"] == cleaned["y_axis"]:
            self.add_error("y_axis", "Choose a different variable for the Y axis.")
        return cleaned

    def has_meaningful_changes(self, fields):
        return self.schema_has_meaningful_changes(fields) or self.visualization_has_meaningful_changes()

    def schema_has_meaningful_changes(self, fields):
        if self.question is None:
            return True
        if self.cleaned_data["question_text"] != self.question.question_text:
            return True
        return fields != self._initial_schema

    def visualization_has_meaningful_changes(self):
        if self.question is None:
            return True
        from .services import housing_scatter_config
        config = housing_scatter_config(self.question)
        if self.cleaned_data["x_axis"] != config["x_axis"]:
            return True
        if self.cleaned_data["y_axis"] != config["y_axis"]:
            return True
        return False

    def serialize_fields(self, field_formset):
        from .services import HOUSING_FIELD_CHOICE
        existing_keys = {field["key"] for field in self._initial_schema}
        used_keys = set()
        rows = []
        for form in field_formset.ordered_forms:
            data = form.cleaned_data
            if not data or data.get("delete"):
                continue
            label = (data.get("label") or "").strip()
            if not label:
                continue
            key = (data.get("key") or "").strip()
            if not key or key not in existing_keys:
                key = _slugify_field_key(label, used_keys | existing_keys)
            used_keys.add(key)
            options = []
            if data.get("field_type") == HOUSING_FIELD_CHOICE:
                seen = set()
                for option_label in data.get("options_text", []):
                    value = slugify(option_label).replace("-", "_") or "option"
                    base_value = value
                    suffix = 2
                    while value in seen:
                        value = f"{base_value}_{suffix}"
                        suffix += 1
                    seen.add(value)
                    options.append({"value": value, "label": option_label})
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "type": data["field_type"],
                    "required": bool(data.get("required")),
                    "help_text": (data.get("help_text") or "").strip(),
                    "options": options,
                }
            )
        return rows


class ExportFilterForm(forms.Form):
    activity = forms.ChoiceField(
        choices=[
            ("", "All activities"),
            ("relationship", "Relationship survey"),
            ("housing", "Housing entries"),
        ],
        required=False,
    )
    date_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

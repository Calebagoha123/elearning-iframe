from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils.text import slugify


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
    district = forms.CharField(max_length=120)
    monthly_rent = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    rooms = forms.DecimalField(min_value=0, max_digits=4, decimal_places=1)
    size_sqm = forms.DecimalField(
        min_value=0,
        max_digits=8,
        decimal_places=2,
        required=False,
        label="Size in square metres",
    )


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


class HousingEditForm(forms.Form):
    question_text = forms.CharField(
        label="Question wording",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    x_axis = forms.ChoiceField(label="Scatter X-axis")
    y_axis = forms.ChoiceField(label="Scatter Y-axis")

    def __init__(self, *args, question=None, **kwargs):
        from .services import HOUSING_AXIS_CHOICES, housing_scatter_config
        super().__init__(*args, **kwargs)
        self.question = question
        self.fields["x_axis"].choices = HOUSING_AXIS_CHOICES
        self.fields["y_axis"].choices = HOUSING_AXIS_CHOICES
        if question is not None and not self.is_bound:
            config = housing_scatter_config(question)
            self.fields["question_text"].initial = question.question_text
            self.fields["x_axis"].initial = config["x_axis"]
            self.fields["y_axis"].initial = config["y_axis"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("x_axis") and cleaned.get("y_axis") and cleaned["x_axis"] == cleaned["y_axis"]:
            self.add_error("y_axis", "Choose a different variable for the Y axis.")
        return cleaned

    def has_meaningful_changes(self):
        if self.question is None:
            return True
        from .services import housing_scatter_config
        config = housing_scatter_config(self.question)
        return (
            self.cleaned_data["question_text"] != self.question.question_text
            or self.cleaned_data["x_axis"] != config["x_axis"]
            or self.cleaned_data["y_axis"] != config["y_axis"]
        )


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

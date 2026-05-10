from django import forms
from django.forms import BaseFormSet, formset_factory


class RelationshipForm(forms.Form):
    relationship = forms.ChoiceField(label="Relationship", required=False)
    other_relationship = forms.CharField(
        label="Other relationship",
        max_length=120,
        required=False,
    )

    def __init__(self, *args, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        base_choices = [(choice["value"], choice["label"]) for choice in choices or []]
        self.fields["relationship"].choices = [("", "Select a relationship")] + base_choices + [("other", "Other")]
        self.fields["relationship"].widget.attrs["data-relationship-select"] = ""
        self.fields["other_relationship"].widget.attrs["placeholder"] = "Describe the relationship"

    def clean(self):
        cleaned = super().clean()
        relationship = cleaned.get("relationship")
        other = (cleaned.get("other_relationship") or "").strip()
        if other and not relationship:
            self.add_error("relationship", "Select Other before entering a custom relationship.")
        if relationship == "other" and not other:
            self.add_error("other_relationship", "Enter the relationship type.")
        return cleaned

    def relationship_label(self):
        relationship = self.cleaned_data["relationship"]
        if relationship == "other":
            return self.cleaned_data["other_relationship"].strip()
        return dict(self.fields["relationship"].choices).get(relationship, relationship)


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

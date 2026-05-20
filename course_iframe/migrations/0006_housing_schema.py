from django.db import migrations


DEFAULT_HOUSING_FIELDS = [
    {
        "key": "district",
        "label": "District",
        "type": "place",
        "required": True,
        "help_text": "Start typing a real district or city, then pick a suggestion.",
        "options": [],
    },
    {
        "key": "monthly_rent",
        "label": "Monthly rent",
        "type": "currency_eur",
        "required": True,
        "help_text": "Total monthly rent in euros.",
        "options": [],
    },
    {
        "key": "rooms",
        "label": "Number of rooms",
        "type": "decimal",
        "required": True,
        "help_text": "Use .5 for a half room (e.g. 2.5).",
        "options": [],
    },
    {
        "key": "size_sqm",
        "label": "Size (m²)",
        "type": "decimal",
        "required": False,
        "help_text": "Optional. Floor area in square metres.",
        "options": [],
    },
]


def seed_housing_schema(apps, schema_editor):
    Question = apps.get_model("course_iframe", "Question")

    for question in Question.objects.filter(activity__slug="housing"):
        options = dict(question.options_json or {})
        if not options.get("fields"):
            options["fields"] = list(DEFAULT_HOUSING_FIELDS)
        question.options_json = options
        question.save(update_fields=["options_json"])


def unseed_housing_schema(apps, schema_editor):
    Question = apps.get_model("course_iframe", "Question")
    for question in Question.objects.filter(activity__slug="housing"):
        options = dict(question.options_json or {})
        options.pop("fields", None)
        question.options_json = options
        question.save(update_fields=["options_json"])


class Migration(migrations.Migration):
    dependencies = [
        ("course_iframe", "0005_alter_participant_options"),
    ]

    operations = [
        migrations.RunPython(seed_housing_schema, unseed_housing_schema),
    ]

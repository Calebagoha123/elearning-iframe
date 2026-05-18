from django.db import migrations


def set_default_relationship_likert(apps, schema_editor):
    Question = apps.get_model("course_iframe", "Question")
    for question in Question.objects.filter(activity__slug="relationship", slug="relationship_type"):
        options = dict(question.options_json or {})
        options.setdefault("likert_points", 5)
        question.options_json = options
        question.save(update_fields=["options_json"])


def unset_default_relationship_likert(apps, schema_editor):
    Question = apps.get_model("course_iframe", "Question")
    for question in Question.objects.filter(activity__slug="relationship", slug="relationship_type"):
        options = dict(question.options_json or {})
        options.pop("likert_points", None)
        question.options_json = options
        question.save(update_fields=["options_json"])


class Migration(migrations.Migration):
    dependencies = [
        ("course_iframe", "0003_rename_course_ifra_activit_419eaf_idx_course_ifra_activit_cb364f_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(set_default_relationship_likert, unset_default_relationship_likert),
    ]

from django.db import migrations, models
from django.db.models import Count, Q


def keep_latest_active_question(apps, schema_editor):
    Question = apps.get_model("course_iframe", "Question")
    duplicate_groups = (
        Question.objects.filter(is_active=True)
        .values("activity_id", "slug")
        .annotate(active_count=Count("id"))
        .filter(active_count__gt=1)
    )
    for group in duplicate_groups:
        active_questions = Question.objects.filter(
            activity_id=group["activity_id"],
            slug=group["slug"],
            is_active=True,
        ).order_by("-version", "-id")
        keep = active_questions.first()
        active_questions.exclude(pk=keep.pk).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("course_iframe", "0006_housing_schema"),
    ]

    operations = [
        migrations.RunPython(keep_latest_active_question, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(
                condition=Q(is_active=True),
                fields=("activity", "slug"),
                name="unique_active_question_per_activity",
            ),
        ),
    ]

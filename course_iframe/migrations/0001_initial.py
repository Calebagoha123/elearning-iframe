from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Activity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("title", models.CharField(max_length=160)),
                ("activity_type", models.CharField(max_length=80)),
                ("is_active", models.BooleanField(default=True)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name_plural": "activities",
                "ordering": ["slug"],
            },
        ),
        migrations.CreateModel(
            name="Course",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("course_id", models.CharField(max_length=120, unique=True)),
                ("course_name", models.CharField(blank=True, max_length=255)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["course_id"],
            },
        ),
        migrations.CreateModel(
            name="ExportLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("export_type", models.CharField(max_length=80)),
                ("file_format", models.CharField(max_length=20)),
                ("activity_slug", models.CharField(blank=True, max_length=80)),
                ("date_start", models.DateField(blank=True, null=True)),
                ("date_end", models.DateField(blank=True, null=True)),
                ("row_count", models.PositiveIntegerField(default=0)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_date"],
            },
        ),
        migrations.CreateModel(
            name="Participant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("participant_id", models.CharField(max_length=160)),
                ("optional_username", models.CharField(blank=True, max_length=160, null=True)),
                ("has_research_consent", models.BooleanField(default=False)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                ("last_used", models.DateTimeField(default=django.utils.timezone.now)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participants", to="course_iframe.course")),
            ],
            options={
                "ordering": ["course__course_id", "participant_id"],
            },
        ),
        migrations.CreateModel(
            name="Question",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=80)),
                ("question_text", models.TextField()),
                ("field_type", models.CharField(choices=[("text", "Text"), ("choice", "Choice"), ("housing", "Housing")], max_length=40)),
                ("options_json", models.JSONField(blank=True, default=dict)),
                ("version", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                ("activity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="questions", to="course_iframe.activity")),
            ],
            options={
                "ordering": ["activity__slug", "slug", "-version"],
            },
        ),
        migrations.CreateModel(
            name="ConsentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consent_type", models.CharField(max_length=80)),
                ("consented", models.BooleanField()),
                ("consented_date", models.DateTimeField(auto_now_add=True)),
                ("withdrawn_at", models.DateTimeField(blank=True, null=True)),
                ("consent_text_version", models.CharField(max_length=40)),
                ("participant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consent_records", to="course_iframe.participant")),
            ],
            options={
                "ordering": ["-consented_date"],
            },
        ),
        migrations.CreateModel(
            name="Response",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("question_version", models.PositiveIntegerField()),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("activity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="responses", to="course_iframe.activity")),
                ("participant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="responses", to="course_iframe.participant")),
                ("question", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="responses", to="course_iframe.question")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="participant",
            constraint=models.UniqueConstraint(fields=("course", "participant_id"), name="unique_participant_per_course"),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(fields=("activity", "slug", "version"), name="unique_question_version_per_activity"),
        ),
        migrations.AddIndex(
            model_name="response",
            index=models.Index(fields=["activity", "created_at"], name="course_ifra_activit_419eaf_idx"),
        ),
        migrations.AddIndex(
            model_name="response",
            index=models.Index(fields=["participant", "activity"], name="course_ifra_partici_c66abb_idx"),
        ),
    ]

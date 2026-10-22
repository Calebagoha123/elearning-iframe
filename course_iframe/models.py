from django.db import models
from django.utils import timezone


class Course(models.Model):
    course_id = models.CharField(max_length=120, unique=True)
    course_name = models.CharField(max_length=255, blank=True)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["course_id"]

    def __str__(self):
        return self.course_name or self.course_id


class Participant(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="participants")
    participant_id = models.CharField(max_length=160)
    optional_username = models.CharField(max_length=160, blank=True, null=True)
    has_research_consent = models.BooleanField(default=False)
    created_date = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "participant_id"],
                name="unique_participant_per_course",
            )
        ]
        ordering = ["participant_id"]

    def __str__(self):
        return self.participant_id


class Activity(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=160)
    activity_type = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["slug"]

    def __str__(self):
        return self.title


class Question(models.Model):
    FIELD_TEXT = "text"
    FIELD_CHOICE = "choice"
    FIELD_HOUSING = "housing"
    FIELD_ACTIVITY = "activity"
    FIELD_TYPES = [
        (FIELD_TEXT, "Text"),
        (FIELD_CHOICE, "Choice"),
        (FIELD_HOUSING, "Housing"),
        (FIELD_ACTIVITY, "Activity questions"),
    ]

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="questions")
    slug = models.SlugField(max_length=80)
    question_text = models.TextField()
    field_type = models.CharField(max_length=40, choices=FIELD_TYPES)
    options_json = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "slug", "version"],
                name="unique_question_version_per_activity",
            ),
            models.UniqueConstraint(
                fields=["activity", "slug"],
                condition=models.Q(is_active=True),
                name="unique_active_question_per_activity",
            ),
        ]
        ordering = ["activity__slug", "slug", "-version"]

    def __str__(self):
        return f"{self.activity.slug}:{self.slug}:v{self.version}"


class Response(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="responses")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="responses")
    question_version = models.PositiveIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["activity", "created_at"]),
            models.Index(fields=["participant", "activity"]),
        ]

    def __str__(self):
        return f"{self.participant} {self.activity.slug} {self.created_at:%Y-%m-%d}"


class ConsentRecord(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="consent_records")
    consent_type = models.CharField(max_length=80)
    consented = models.BooleanField()
    consented_date = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(blank=True, null=True)
    consent_text_version = models.CharField(max_length=40)

    class Meta:
        ordering = ["-consented_date"]


class ExportLog(models.Model):
    export_type = models.CharField(max_length=80)
    file_format = models.CharField(max_length=20)
    activity_slug = models.CharField(max_length=80, blank=True)
    date_start = models.DateField(blank=True, null=True)
    date_end = models.DateField(blank=True, null=True)
    row_count = models.PositiveIntegerField(default=0)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_date"]

    def __str__(self):
        return f"{self.export_type} {self.file_format} {self.created_date:%Y-%m-%d %H:%M}"

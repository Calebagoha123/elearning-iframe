from django.contrib import admin

from .models import Activity, ConsentRecord, ExportLog, Participant, Question, Response


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ["participant_id", "has_research_consent", "last_used"]
    list_filter = ["has_research_consent"]
    search_fields = ["participant_id", "optional_username"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["slug", "title", "activity_type", "is_active"]
    list_filter = ["is_active"]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["slug", "activity", "field_type", "version", "is_active"]
    list_filter = ["activity", "field_type", "is_active"]


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ["participant", "activity", "question", "question_version", "created_at"]
    list_filter = ["activity", "question_version", "created_at"]
    search_fields = ["participant__participant_id"]


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = ["participant", "consent_type", "consented", "consented_date", "withdrawn_at"]
    list_filter = ["consent_type", "consented"]


@admin.register(ExportLog)
class ExportLogAdmin(admin.ModelAdmin):
    list_display = ["export_type", "file_format", "activity_slug", "row_count", "created_date"]
    list_filter = ["file_format", "activity_slug"]

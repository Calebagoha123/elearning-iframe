import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import Activity, Course, Participant, Question, Response


RELATIONSHIP_SLUG = "relationship"
HOUSING_SLUG = "housing"
RELATIONSHIP_QUESTION_SLUG = "relationship_type"
HOUSING_QUESTION_SLUG = "housing_property"
SESSION_COURSE_ID = "course_id"
SESSION_PARTICIPANT_ID = "participant_id"


def make_iframe_signature(course_id, participant_id):
    message = json.dumps(
        {
            "course_id": course_id,
            "participant_id": participant_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    secret = settings.IFRAME_SIGNING_SECRET.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def valid_iframe_signature(course_id, participant_id, signature):
    if not course_id or not participant_id or not signature:
        return False
    expected = make_iframe_signature(course_id, participant_id)
    return hmac.compare_digest(expected, signature)


def remember_participant(request, participant):
    request.session[SESSION_COURSE_ID] = participant.course.course_id
    request.session[SESSION_PARTICIPANT_ID] = participant.participant_id


def get_or_create_participant(course_id, participant_id):
    course, _ = Course.objects.get_or_create(course_id=course_id)
    participant, _ = Participant.objects.get_or_create(
        course=course,
        participant_id=participant_id,
        defaults={"last_used": timezone.now()},
    )
    Participant.objects.filter(pk=participant.pk).update(last_used=timezone.now())
    participant.refresh_from_db()
    return participant


def participant_from_session(request):
    course_id = request.session.get(SESSION_COURSE_ID)
    participant_id = request.session.get(SESSION_PARTICIPANT_ID)
    if not course_id or not participant_id:
        return None
    try:
        return Participant.objects.select_related("course").get(
            course__course_id=course_id,
            participant_id=participant_id,
        )
    except Participant.DoesNotExist:
        return None


def active_question(activity_slug, question_slug):
    return Question.objects.select_related("activity").get(
        activity__slug=activity_slug,
        slug=question_slug,
        is_active=True,
        activity__is_active=True,
    )


def save_relationship_responses(participant, question, relationships):
    with transaction.atomic():
        Response.objects.filter(participant=participant, question=question).delete()
        return Response.objects.bulk_create(
            [
                Response(
                    participant=participant,
                    activity=question.activity,
                    question=question,
                    question_version=question.version,
                    payload=relationship,
                )
                for relationship in relationships
            ]
        )


def save_relationship_response(participant, question, relationship_value, relationship_label):
    return save_relationship_responses(
        participant,
        question,
        [
            {
                "relationship_value": relationship_value,
                "relationship_label": relationship_label,
            }
        ],
    )


def save_housing_response(participant, question, cleaned_data):
    return Response.objects.create(
        participant=participant,
        activity=question.activity,
        question=question,
        question_version=question.version,
        payload={
            "district": cleaned_data["district"].strip(),
            "monthly_rent": str(cleaned_data["monthly_rent"]),
            "rooms": str(cleaned_data["rooms"]),
            "size_sqm": str(cleaned_data["size_sqm"]) if cleaned_data.get("size_sqm") else "",
        },
    )


def relationship_counts(course):
    rows = (
        Response.objects.filter(
            participant__course=course,
            activity__slug=RELATIONSHIP_SLUG,
        )
        .values("payload")
        .annotate(count=Count("id"))
    )
    counts = {}
    for row in rows:
        label = row["payload"].get("relationship_label", "").strip()
        if label:
            counts[label] = counts.get(label, 0) + row["count"]
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))


def housing_comparison_rows(course, participant):
    responses = Response.objects.filter(
        participant__course=course,
        activity__slug=HOUSING_SLUG,
    ).select_related("participant")
    cohort = {}
    student = {}
    for response in responses:
        district = response.payload.get("district", "").strip()
        if not district:
            continue
        rent = Decimal(str(response.payload.get("monthly_rent") or "0"))
        cohort.setdefault(district, []).append(rent)
        if response.participant_id == participant.id:
            student.setdefault(district, []).append(rent)

    rows = []
    max_value = Decimal("1")
    for district in sorted(cohort):
        cohort_avg = sum(cohort[district]) / len(cohort[district])
        student_values = student.get(district, [])
        student_avg = sum(student_values) / len(student_values) if student_values else None
        if cohort_avg > max_value:
            max_value = cohort_avg
        if student_avg and student_avg > max_value:
            max_value = student_avg
        rows.append(
            {
                "district": district,
                "cohort_average": cohort_avg,
                "student_average": student_avg,
                "cohort_count": len(cohort[district]),
                "student_count": len(student_values),
            }
        )

    for row in rows:
        row["cohort_width"] = int((row["cohort_average"] / max_value) * 100)
        row["student_width"] = (
            int((row["student_average"] / max_value) * 100)
            if row["student_average"] is not None
            else 0
        )
    return rows


def participant_count_for_course_activity(course, activity_slug):
    return (
        Response.objects.filter(
            participant__course=course,
            activity__slug=activity_slug,
        )
        .values("participant_id")
        .distinct()
        .count()
    )


def mvp_activities():
    return Activity.objects.filter(slug__in=[RELATIONSHIP_SLUG, HOUSING_SLUG]).order_by("slug")

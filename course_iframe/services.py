import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import Activity, Course, Participant, Question, Response


DEFAULT_COURSE_ID = "default"
RELATIONSHIP_SLUG = "relationship"
HOUSING_SLUG = "housing"
RELATIONSHIP_QUESTION_SLUG = "relationship_type"
HOUSING_QUESTION_SLUG = "housing_property"
SESSION_PARTICIPANT_ID = "participant_id"
DEFAULT_LIKERT_POINTS = 5
LIKERT_CHOICES = {
    3: [
        ("lower", "Lower than theirs"),
        ("same", "Same"),
        ("higher", "Higher than theirs"),
    ],
    5: [
        ("much_lower", "Much lower than theirs"),
        ("lower", "Lower than theirs"),
        ("same", "Same"),
        ("higher", "Higher than theirs"),
        ("much_higher", "Much higher than theirs"),
    ],
    7: [
        ("much_lower", "Much lower than theirs"),
        ("lower", "Lower than theirs"),
        ("slightly_lower", "Slightly lower than theirs"),
        ("same", "Same"),
        ("slightly_higher", "Slightly higher than theirs"),
        ("higher", "Higher than theirs"),
        ("much_higher", "Much higher than theirs"),
    ],
}

RELATIONSHIP_PALETTE = [
    "#0072b2",
    "#e69f00",
    "#009e73",
    "#cc79a7",
    "#56b4e9",
    "#d55e00",
    "#882255",
    "#332288",
    "#117733",
    "#aa4499",
    "#44aa99",
    "#999933",
]
RELATIONSHIP_OTHER_COLOR = "#7a7a7a"

HOUSING_AXIS_CHOICES = [
    ("monthly_rent", "Total monthly rent"),
    ("rent_per_sqm", "Rent per square metre"),
    ("rooms", "Number of rooms"),
    ("size_sqm", "Size (square metres)"),
]
HOUSING_AXIS_VALUES = {key for key, _ in HOUSING_AXIS_CHOICES}
DEFAULT_HOUSING_X_AXIS = "rooms"
DEFAULT_HOUSING_Y_AXIS = "monthly_rent"


def make_iframe_signature(course_id, participant_id):
    message = json.dumps(
        {
            "participant_id": participant_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    secret = settings.IFRAME_SIGNING_SECRET.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def valid_iframe_signature(course_id, participant_id, signature):
    if not participant_id or not signature:
        return False
    expected = make_iframe_signature(course_id, participant_id)
    if hmac.compare_digest(expected, signature):
        return True

    legacy_message = json.dumps(
        {
            "course_id": course_id,
            "participant_id": participant_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    legacy_expected = hmac.new(
        settings.IFRAME_SIGNING_SECRET.encode(),
        legacy_message,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(legacy_expected, signature)


def remember_participant(request, participant):
    request.session[SESSION_PARTICIPANT_ID] = participant.participant_id


def default_course():
    course, _ = Course.objects.get_or_create(course_id=DEFAULT_COURSE_ID)
    return course


def get_or_create_participant(participant_id):
    course = default_course()
    participant, _ = Participant.objects.get_or_create(
        course=course,
        participant_id=participant_id,
        defaults={"last_used": timezone.now()},
    )
    Participant.objects.filter(pk=participant.pk).update(last_used=timezone.now())
    participant.refresh_from_db()
    return participant


def participant_from_session(request):
    participant_id = request.session.get(SESSION_PARTICIPANT_ID)
    if not participant_id:
        return None
    try:
        return Participant.objects.select_related("course").get(
            course=default_course(),
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


def create_question_version(current_question, question_text, options):
    with transaction.atomic():
        Question.objects.filter(pk=current_question.pk).update(is_active=False)
        latest_version = (
            Question.objects.filter(
                activity=current_question.activity,
                slug=current_question.slug,
            )
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
            or current_question.version
        )
        return Question.objects.create(
            activity=current_question.activity,
            slug=current_question.slug,
            question_text=question_text,
            field_type=current_question.field_type,
            options_json=options,
            version=latest_version + 1,
            is_active=True,
        )


def relationship_likert_points(question):
    points = question.options_json.get("likert_points", DEFAULT_LIKERT_POINTS)
    try:
        points = int(points)
    except (TypeError, ValueError):
        return DEFAULT_LIKERT_POINTS
    return points if points in LIKERT_CHOICES else DEFAULT_LIKERT_POINTS


def relationship_likert_choices(question):
    points = relationship_likert_points(question)
    overrides = (question.options_json or {}).get("likert_labels", {}).get(str(points), {})
    return [(key, overrides.get(key, default_label)) for key, default_label in LIKERT_CHOICES[points]]


def update_relationship_likert_points(points):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    options = dict(question.options_json)
    options["likert_points"] = int(points)
    return create_question_version(question, question.question_text, options)


def update_relationship_settings(question_text, likert_points, likert_labels, relationship_choices=None):
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    options = dict(question.options_json or {})
    if relationship_choices is not None:
        options["choices"] = list(relationship_choices)
    options["likert_points"] = int(likert_points)
    stored_labels = dict(options.get("likert_labels") or {})
    stored_labels[str(int(likert_points))] = dict(likert_labels)
    options["likert_labels"] = stored_labels
    return create_question_version(question, question_text, options)


def relationship_color_map(question):
    choices = (question.options_json or {}).get("choices", [])
    return {
        choice["value"]: RELATIONSHIP_PALETTE[index % len(RELATIONSHIP_PALETTE)]
        for index, choice in enumerate(choices)
        if choice.get("value")
    }


def palette_color_for_relationship(label, is_other=False, relationship_value="", colors_by_value=None):
    if is_other or relationship_value == "other":
        return RELATIONSHIP_OTHER_COLOR
    if colors_by_value and relationship_value in colors_by_value:
        return colors_by_value[relationship_value]
    return RELATIONSHIP_PALETTE[0]


def housing_scatter_config(question):
    viz = (question.options_json or {}).get("viz") or {}
    x_axis = viz.get("x_axis", DEFAULT_HOUSING_X_AXIS)
    y_axis = viz.get("y_axis", DEFAULT_HOUSING_Y_AXIS)
    if x_axis not in HOUSING_AXIS_VALUES:
        x_axis = DEFAULT_HOUSING_X_AXIS
    if y_axis not in HOUSING_AXIS_VALUES:
        y_axis = DEFAULT_HOUSING_Y_AXIS
    return {"x_axis": x_axis, "y_axis": y_axis}


def housing_axis_label(axis_key):
    return dict(HOUSING_AXIS_CHOICES).get(axis_key, axis_key)


def update_housing_settings(question_text, x_axis, y_axis):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    options = dict(question.options_json or {})
    options["viz"] = {
        "x_axis": x_axis if x_axis in HOUSING_AXIS_VALUES else DEFAULT_HOUSING_X_AXIS,
        "y_axis": y_axis if y_axis in HOUSING_AXIS_VALUES else DEFAULT_HOUSING_Y_AXIS,
    }
    return create_question_version(question, question_text, options)


def save_relationship_responses(participant, question, relationships):
    likert_points = relationship_likert_points(question)
    with transaction.atomic():
        Response.objects.filter(participant=participant, question=question).delete()
        return Response.objects.bulk_create(
            [
                Response(
                    participant=participant,
                    activity=question.activity,
                    question=question,
                    question_version=question.version,
                    payload={
                        **relationship,
                        "income_likert_points": likert_points,
                    },
                )
                for relationship in relationships
            ]
        )


def save_relationship_response(
    participant,
    question,
    relationship_value,
    relationship_label,
    income_comparison_value="same",
    income_comparison_label="Same",
):
    return save_relationship_responses(
        participant,
        question,
        [
            {
                "relationship_value": relationship_value,
                "relationship_label": relationship_label,
                "income_comparison_value": income_comparison_value,
                "income_comparison_label": income_comparison_label,
                "income_likert_points": relationship_likert_points(question),
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


def relationship_counts(question):
    responses = Response.objects.filter(question=question)
    rows = responses.values("payload").annotate(count=Count("id"))
    counts = {}
    for row in rows:
        payload = row["payload"] or {}
        label = payload.get("relationship_label", "").strip()
        if label:
            item = counts.setdefault(
                label,
                {
                    "label": label,
                    "count": 0,
                    "is_other": False,
                    "value": payload.get("relationship_value", ""),
                },
            )
            item["count"] += row["count"]
            item["is_other"] = item["is_other"] or payload.get("relationship_value") == "other"
            if not item["value"]:
                item["value"] = payload.get("relationship_value", "")
    return sorted(counts.values(), key=lambda item: (-item["count"], item["label"].lower()))


def relationship_income_counts(question):
    rows = (
        Response.objects.filter(question=question)
        .values("payload")
        .annotate(count=Count("id"))
    )
    counts = {}
    for row in rows:
        value = row["payload"].get("income_comparison_value", "").strip()
        if value:
            counts[value] = counts.get(value, 0) + row["count"]

    return [
        {
            "value": value,
            "label": label,
            "count": counts.get(value, 0),
        }
        for value, label in relationship_likert_choices(question)
    ]


def _payload_decimal(payload, key):
    raw = payload.get(key)
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value


def _housing_value(payload, axis_key):
    if axis_key == "rent_per_sqm":
        rent = _payload_decimal(payload, "monthly_rent")
        size = _payload_decimal(payload, "size_sqm")
        if rent is None or not size or size == 0:
            return None
        return rent / size
    return _payload_decimal(payload, axis_key)


def housing_scatter_points(question, participant, x_axis, y_axis):
    responses = Response.objects.filter(question=question).select_related("participant")
    points = []
    for response in responses:
        payload = response.payload or {}
        x_value = _housing_value(payload, x_axis)
        y_value = _housing_value(payload, y_axis)
        if x_value is None or y_value is None:
            continue
        points.append(
            {
                "x": float(x_value),
                "y": float(y_value),
                "district": (payload.get("district") or "").strip(),
                "is_yours": bool(participant and response.participant_id == participant.id),
            }
        )
    return points


def relationship_segmented_bars(question):
    colors_by_value = relationship_color_map(question)
    responses = Response.objects.filter(question=question)
    rows = responses.values("payload").annotate(count=Count("id"))
    bucket = {key: {} for key, _ in relationship_likert_choices(question)}
    for row in rows:
        payload = row["payload"] or {}
        income_value = (payload.get("income_comparison_value") or "").strip()
        label = (payload.get("relationship_label") or "").strip()
        relationship_value = (payload.get("relationship_value") or "").strip()
        if not income_value or not label or income_value not in bucket:
            continue
        item = bucket[income_value].setdefault(
            label,
            {
                "count": 0,
                "is_other": False,
                "value": relationship_value,
            },
        )
        item["count"] += row["count"]
        item["is_other"] = item["is_other"] or relationship_value == "other"
        if not item["value"]:
            item["value"] = relationship_value

    columns = []
    column_max = 0
    for key, display_label in relationship_likert_choices(question):
        segments_raw = bucket.get(key, {})
        segments = []
        total = 0
        for label, item in sorted(segments_raw.items(), key=lambda item: (-item[1]["count"], item[0].lower())):
            segments.append(
                {
                    "label": label,
                    "count": item["count"],
                    "color": palette_color_for_relationship(
                        label,
                        item["is_other"],
                        item["value"],
                        colors_by_value,
                    ),
                }
            )
            total += item["count"]
        if total > column_max:
            column_max = total
        columns.append(
            {
                "key": key,
                "label": display_label,
                "total": total,
                "segments": segments,
            }
        )

    legend_seen = {}
    for column in columns:
        for segment in column["segments"]:
            legend_seen.setdefault(segment["label"], segment["color"])
    legend = [
        {"label": label, "color": color}
        for label, color in sorted(legend_seen.items(), key=lambda item: item[0].lower())
    ]
    return {"columns": columns, "column_max": column_max, "legend": legend}


def participant_count_for_question(question):
    return (
        Response.objects.filter(question=question)
        .values("participant_id")
        .distinct()
        .count()
    )


def mvp_activities():
    return Activity.objects.filter(slug__in=[RELATIONSHIP_SLUG, HOUSING_SLUG]).order_by("slug")

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

HOUSING_FIELD_TEXT = "text"
HOUSING_FIELD_INTEGER = "integer"
HOUSING_FIELD_DECIMAL = "decimal"
HOUSING_FIELD_CURRENCY_EUR = "currency_eur"
HOUSING_FIELD_PLACE = "place"
HOUSING_FIELD_CHOICE = "choice"

HOUSING_FIELD_TYPE_CHOICES = [
    (HOUSING_FIELD_PLACE, "Place (verified via OpenStreetMap)"),
    (HOUSING_FIELD_CURRENCY_EUR, "Currency (€)"),
    (HOUSING_FIELD_DECIMAL, "Number (decimal)"),
    (HOUSING_FIELD_INTEGER, "Number (integer)"),
    (HOUSING_FIELD_TEXT, "Free text"),
    (HOUSING_FIELD_CHOICE, "Single choice"),
]
HOUSING_FIELD_TYPE_VALUES = {key for key, _ in HOUSING_FIELD_TYPE_CHOICES}
HOUSING_NUMERIC_FIELD_TYPES = {
    HOUSING_FIELD_INTEGER,
    HOUSING_FIELD_DECIMAL,
    HOUSING_FIELD_CURRENCY_EUR,
}

DEFAULT_HOUSING_FIELDS = [
    {
        "key": "district",
        "label": "District",
        "type": HOUSING_FIELD_PLACE,
        "required": True,
        "help_text": "Start typing a real district or city, then pick a suggestion.",
        "options": [],
    },
    {
        "key": "monthly_rent",
        "label": "Monthly rent",
        "type": HOUSING_FIELD_CURRENCY_EUR,
        "required": True,
        "help_text": "Total monthly rent in euros.",
        "options": [],
    },
    {
        "key": "rooms",
        "label": "Number of rooms",
        "type": HOUSING_FIELD_DECIMAL,
        "required": True,
        "help_text": "Use .5 for a half room (e.g. 2.5).",
        "options": [],
    },
    {
        "key": "size_sqm",
        "label": "Size (m²)",
        "type": HOUSING_FIELD_DECIMAL,
        "required": False,
        "help_text": "Optional. Floor area in square metres.",
        "options": [],
    },
]

DEFAULT_HOUSING_X_AXIS = "rooms"
DEFAULT_HOUSING_Y_AXIS = "monthly_rent"
RENT_PER_SQM_KEY = "rent_per_sqm"


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


def housing_schema(question):
    options = question.options_json or {}
    fields = options.get("fields")
    if not fields:
        fields = DEFAULT_HOUSING_FIELDS
    return [dict(field) for field in fields]


def housing_field_keys(question):
    return [field["key"] for field in housing_schema(question)]


def housing_place_field_keys(question):
    return [
        field["key"]
        for field in housing_schema(question)
        if field["type"] == HOUSING_FIELD_PLACE
    ]


def housing_numeric_fields(question):
    return [
        field
        for field in housing_schema(question)
        if field["type"] in HOUSING_NUMERIC_FIELD_TYPES
    ]


def housing_axis_choices(question):
    numeric = housing_numeric_fields(question)
    choices = [(field["key"], field["label"]) for field in numeric]
    numeric_keys = {field["key"] for field in numeric}
    if {"monthly_rent", "size_sqm"}.issubset(numeric_keys):
        choices.append((RENT_PER_SQM_KEY, "Rent per square metre"))
    return choices


def housing_axis_label(question, axis_key):
    return dict(housing_axis_choices(question)).get(axis_key, axis_key)


def housing_scatter_config(question):
    viz = (question.options_json or {}).get("viz") or {}
    ordered_axes = [key for key, _ in housing_axis_choices(question)]
    valid_axes = set(ordered_axes)

    def pick(preferred, fallback_pool):
        if preferred in valid_axes:
            return preferred
        for candidate in fallback_pool:
            if candidate in valid_axes:
                return candidate
        return ordered_axes[0] if ordered_axes else preferred

    x_axis = viz.get("x_axis")
    if x_axis not in valid_axes:
        x_axis = pick(DEFAULT_HOUSING_X_AXIS, ordered_axes)
    y_axis = viz.get("y_axis")
    if y_axis not in valid_axes or y_axis == x_axis:
        remaining = [key for key in ordered_axes if key != x_axis]
        y_axis = pick(DEFAULT_HOUSING_Y_AXIS, remaining) if DEFAULT_HOUSING_Y_AXIS != x_axis else (remaining[0] if remaining else DEFAULT_HOUSING_Y_AXIS)
    return {"x_axis": x_axis, "y_axis": y_axis}


def update_housing_visualization(x_axis, y_axis):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    options = dict(question.options_json or {})
    ordered_axes = [key for key, _ in housing_axis_choices(question)]
    valid_axes = set(ordered_axes)
    if not ordered_axes:
        return question
    if x_axis not in valid_axes:
        x_axis = DEFAULT_HOUSING_X_AXIS if DEFAULT_HOUSING_X_AXIS in valid_axes else ordered_axes[0]
    if y_axis not in valid_axes or y_axis == x_axis:
        remaining = [key for key in ordered_axes if key != x_axis]
        if DEFAULT_HOUSING_Y_AXIS in remaining:
            y_axis = DEFAULT_HOUSING_Y_AXIS
        else:
            y_axis = remaining[0] if remaining else x_axis
    options["viz"] = {
        "x_axis": x_axis,
        "y_axis": y_axis,
    }
    Question.objects.filter(pk=question.pk).update(options_json=options)
    question.options_json = options
    return question


def update_housing_schema(question_text, fields, viz=None):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    options = dict(question.options_json or {})
    options["fields"] = list(fields)
    if viz:
        candidate_axes = {field["key"] for field in fields if field["type"] in HOUSING_NUMERIC_FIELD_TYPES}
        numeric_keys = candidate_axes
        if {"monthly_rent", "size_sqm"}.issubset(numeric_keys):
            candidate_axes = candidate_axes | {RENT_PER_SQM_KEY}
        x_axis = viz.get("x_axis") if viz.get("x_axis") in candidate_axes else next(iter(candidate_axes), DEFAULT_HOUSING_X_AXIS)
        y_axis = viz.get("y_axis") if viz.get("y_axis") in candidate_axes and viz.get("y_axis") != x_axis else None
        if y_axis is None:
            remaining = [key for key in candidate_axes if key != x_axis]
            y_axis = remaining[0] if remaining else DEFAULT_HOUSING_Y_AXIS
        options["viz"] = {"x_axis": x_axis, "y_axis": y_axis}
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


def _housing_payload_from_cleaned(question, cleaned_data):
    payload = {}
    for field in housing_schema(question):
        key = field["key"]
        ftype = field["type"]
        value = cleaned_data.get(key)
        if ftype == HOUSING_FIELD_PLACE:
            meta = cleaned_data.get(f"{key}_meta") or {}
            display = (meta.get("display_name") or (value or "")).strip()
            payload[key] = display
            if meta:
                payload[f"{key}_meta"] = {
                    "display_name": meta.get("display_name", display),
                    "osm_type": meta.get("osm_type", ""),
                    "osm_id": str(meta.get("osm_id", "")),
                    "lat": str(meta.get("lat", "")),
                    "lon": str(meta.get("lon", "")),
                }
        elif ftype == HOUSING_FIELD_INTEGER:
            payload[key] = "" if value in (None, "") else str(int(value))
        elif ftype in {HOUSING_FIELD_DECIMAL, HOUSING_FIELD_CURRENCY_EUR}:
            payload[key] = "" if value in (None, "") else str(value)
        else:
            text = (value or "") if isinstance(value, str) else ("" if value in (None, "") else str(value))
            payload[key] = text.strip() if isinstance(text, str) else text
    return payload


def save_housing_response(participant, question, cleaned_data):
    return Response.objects.create(
        participant=participant,
        activity=question.activity,
        question=question,
        question_version=question.version,
        payload=_housing_payload_from_cleaned(question, cleaned_data),
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
    if axis_key == RENT_PER_SQM_KEY:
        rent = _payload_decimal(payload, "monthly_rent")
        size = _payload_decimal(payload, "size_sqm")
        if rent is None or not size or size == 0:
            return None
        return rent / size
    return _payload_decimal(payload, axis_key)


def _housing_label_for_point(question, payload):
    for key in housing_place_field_keys(question):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    fallback = payload.get("district")
    return fallback.strip() if isinstance(fallback, str) else ""


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
                "district": _housing_label_for_point(question, payload),
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

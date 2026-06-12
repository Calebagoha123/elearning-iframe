from decimal import Decimal, InvalidOperation

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

# Colorblind-safe qualitative palette (Okabe-Ito + one Tol wine to reach 9 hues).
# Each pair is distinguishable under deuteranopia, protanopia, and tritanopia.
# Yellow sits last since it has weakest contrast on light surfaces and under the
# diagonal stripe overlay used on the "yours" stack.
RELATIONSHIP_PALETTE = [
    "#0072b2",  # blue
    "#e69f00",  # orange
    "#009e73",  # bluish green
    "#cc79a7",  # reddish purple
    "#56b4e9",  # sky blue
    "#d55e00",  # vermillion
    "#000000",  # black
    "#882255",  # wine (Tol qualitative)
    "#f0e442",  # yellow
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
    """Numeric fields available as axis components (numerator or denominator)."""
    return [(field["key"], field["label"]) for field in housing_numeric_fields(question)]


def _normalize_axis_spec(raw, numeric_keys):
    """Coerce a stored axis (dict, plain string, or legacy 'rent_per_sqm') into a normalized dict.

    Returns ``{"numerator": str, "denominator": str, "label": str}`` with field keys
    validated against ``numeric_keys``. Empty strings mean "not set" (no axis), "no
    denominator" (plain field), or "fall back to derived label".
    """
    label = ""
    if isinstance(raw, dict):
        numerator = (raw.get("numerator") or "").strip()
        denominator = (raw.get("denominator") or "").strip()
        label = (raw.get("label") or "").strip()
    elif raw == RENT_PER_SQM_KEY:
        numerator, denominator = "monthly_rent", "size_sqm"
    elif isinstance(raw, str):
        numerator, denominator = raw, ""
    else:
        numerator, denominator = "", ""
    if numerator not in numeric_keys:
        numerator = ""
    if denominator and (denominator not in numeric_keys or denominator == numerator):
        denominator = ""
    if not denominator:
        label = ""  # custom labels only apply to ratios
    return {"numerator": numerator, "denominator": denominator, "label": label}


def housing_axis_label(question, axis_spec):
    labels = {field["key"]: field["label"] for field in housing_numeric_fields(question)}
    if not isinstance(axis_spec, dict):
        axis_spec = _normalize_axis_spec(axis_spec, set(labels))
    if axis_spec.get("label"):
        return axis_spec["label"]
    numerator = axis_spec.get("numerator") or ""
    denominator = axis_spec.get("denominator") or ""
    num_label = labels.get(numerator, numerator)
    if denominator:
        den_label = labels.get(denominator, denominator)
        return f"{num_label} ÷ {den_label}"
    return num_label


def housing_scatter_config(question):
    viz = (question.options_json or {}).get("viz") or {}
    numeric_keys = {field["key"] for field in housing_numeric_fields(question)}
    ordered_keys = [field["key"] for field in housing_numeric_fields(question)]

    x = _normalize_axis_spec(viz.get("x_axis"), numeric_keys)
    y = _normalize_axis_spec(viz.get("y_axis"), numeric_keys)

    def pick_default(preferred, pool):
        if preferred in pool:
            return preferred
        return pool[0] if pool else ""

    if not x["numerator"]:
        x = {"numerator": pick_default(DEFAULT_HOUSING_X_AXIS, ordered_keys), "denominator": "", "label": ""}
    if not y["numerator"] or y == x:
        remaining = [k for k in ordered_keys if k != x["numerator"]]
        y = {"numerator": pick_default(DEFAULT_HOUSING_Y_AXIS, remaining), "denominator": "", "label": ""}
    return {"x_axis": x, "y_axis": y}


def update_housing_visualization(x_axis, y_axis):
    """Persist axis specs. ``x_axis`` and ``y_axis`` are dicts with numerator/denominator."""
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    options = dict(question.options_json or {})
    numeric_keys = {field["key"] for field in housing_numeric_fields(question)}
    ordered_keys = [field["key"] for field in housing_numeric_fields(question)]
    if not ordered_keys:
        return question

    x = _normalize_axis_spec(x_axis, numeric_keys)
    y = _normalize_axis_spec(y_axis, numeric_keys)

    if not x["numerator"]:
        x = {"numerator": DEFAULT_HOUSING_X_AXIS if DEFAULT_HOUSING_X_AXIS in numeric_keys else ordered_keys[0], "denominator": "", "label": ""}
    if not y["numerator"] or y == x:
        remaining = [k for k in ordered_keys if k != x["numerator"]]
        fallback = DEFAULT_HOUSING_Y_AXIS if DEFAULT_HOUSING_Y_AXIS in remaining else (remaining[0] if remaining else x["numerator"])
        y = {"numerator": fallback, "denominator": "", "label": ""}

    options["viz"] = {"x_axis": x, "y_axis": y}
    Question.objects.filter(pk=question.pk).update(options_json=options)
    question.options_json = options
    return question


def update_housing_schema(question_text, fields, viz=None):
    question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)
    options = dict(question.options_json or {})
    options["fields"] = list(fields)
    if viz:
        numeric_keys = {field["key"] for field in fields if field["type"] in HOUSING_NUMERIC_FIELD_TYPES}
        x = _normalize_axis_spec(viz.get("x_axis"), numeric_keys)
        y = _normalize_axis_spec(viz.get("y_axis"), numeric_keys)
        options["viz"] = {"x_axis": x, "y_axis": y}
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


def _housing_value(payload, axis_spec):
    """Compute the numeric value for a point given an axis spec (dict) or legacy string key."""
    if isinstance(axis_spec, dict):
        numerator_key = axis_spec.get("numerator") or ""
        denominator_key = axis_spec.get("denominator") or ""
    elif axis_spec == RENT_PER_SQM_KEY:
        numerator_key, denominator_key = "monthly_rent", "size_sqm"
    elif isinstance(axis_spec, str):
        numerator_key, denominator_key = axis_spec, ""
    else:
        return None
    if not numerator_key:
        return None
    numerator = _payload_decimal(payload, numerator_key)
    if numerator is None:
        return None
    if not denominator_key:
        return numerator
    denominator = _payload_decimal(payload, denominator_key)
    if not denominator or denominator == 0:
        return None
    return numerator / denominator


def _housing_label_for_point(question, payload):
    for key in housing_place_field_keys(question):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    fallback = payload.get("district")
    return fallback.strip() if isinstance(fallback, str) else ""


def housing_raw_payloads(question, participant, include_identity=False):
    """Per-response numeric values + meta needed for client-side scatter rendering.

    ``include_identity`` attaches each response's ``participant_id`` (pseudoname) so the
    research view can highlight a single respondent client-side. It is left off for the
    student view to avoid leaking other students' pseudonames into the page.
    """
    numeric_keys = [field["key"] for field in housing_numeric_fields(question)]
    responses = Response.objects.filter(question=question).select_related("participant")
    rows = []
    for response in responses:
        payload = response.payload or {}
        values = {}
        for key in numeric_keys:
            value = _payload_decimal(payload, key)
            if value is not None:
                values[key] = float(value)
        row = {
            "values": values,
            "district": _housing_label_for_point(question, payload),
            "is_yours": bool(participant and response.participant_id == participant.id),
        }
        if include_identity:
            row["participant_id"] = response.participant.participant_id
        rows.append(row)
    return rows


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


RELATIONSHIP_DISPLAY_MODES = ("both", "yours", "cohort")
DEFAULT_RELATIONSHIP_DISPLAY_MODE = "cohort"


def relationship_display_mode(question):
    mode = ((question.options_json or {}).get("display") or {}).get("mode")
    return mode if mode in RELATIONSHIP_DISPLAY_MODES else DEFAULT_RELATIONSHIP_DISPLAY_MODE


def update_relationship_display_mode(mode):
    if mode not in RELATIONSHIP_DISPLAY_MODES:
        mode = DEFAULT_RELATIONSHIP_DISPLAY_MODE
    question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
    options = dict(question.options_json or {})
    display = dict(options.get("display") or {})
    display["mode"] = mode
    options["display"] = display
    Question.objects.filter(pk=question.pk).update(options_json=options)
    question.options_json = options
    return question


def _build_relationship_stack(responses_qs, question, colors_by_value):
    """Aggregate responses_qs into per-Likert-column stacks of relationship-typed segments."""
    bucket = {key: {} for key, _ in relationship_likert_choices(question)}
    rows = responses_qs.values("payload").annotate(count=Count("id"))
    for row in rows:
        payload = row["payload"] or {}
        income_value = (payload.get("income_comparison_value") or "").strip()
        label = (payload.get("relationship_label") or "").strip()
        relationship_value = (payload.get("relationship_value") or "").strip()
        if not income_value or not label or income_value not in bucket:
            continue
        item = bucket[income_value].setdefault(
            label,
            {"count": 0, "is_other": False, "value": relationship_value},
        )
        item["count"] += row["count"]
        item["is_other"] = item["is_other"] or relationship_value == "other"
        if not item["value"]:
            item["value"] = relationship_value

    stacks = {}
    for key, _ in relationship_likert_choices(question):
        raw = bucket.get(key, {})
        segments = []
        total = 0
        for label, item in sorted(raw.items(), key=lambda pair: (-pair[1]["count"], pair[0].lower())):
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
        stacks[key] = {"segments": segments, "total": total}
    return stacks


def relationship_segmented_bars(question, participant=None, mode=None):
    """Return per-Likert-column stacks for the configured display mode.

    Each column has a ``yours`` and ``cohort`` stack. Stacks not in scope for the
    current mode (or when no participant is supplied) are empty.
    """
    if mode is None:
        mode = relationship_display_mode(question)
    if mode not in RELATIONSHIP_DISPLAY_MODES:
        mode = DEFAULT_RELATIONSHIP_DISPLAY_MODE

    colors_by_value = relationship_color_map(question)
    cohort_qs = Response.objects.filter(question=question)
    cohort_stacks = _build_relationship_stack(cohort_qs, question, colors_by_value) if mode in ("cohort", "both") else {}

    if participant and mode in ("yours", "both"):
        yours_qs = Response.objects.filter(question=question, participant=participant)
        yours_stacks = _build_relationship_stack(yours_qs, question, colors_by_value)
    else:
        yours_stacks = {}

    empty_stack = {"segments": [], "total": 0}
    columns = []
    column_max = 0
    for key, display_label in relationship_likert_choices(question):
        cohort = cohort_stacks.get(key, empty_stack)
        yours = yours_stacks.get(key, empty_stack)
        column_max = max(column_max, cohort["total"], yours["total"])
        columns.append(
            {
                "key": key,
                "label": display_label,
                "cohort": cohort,
                "yours": yours,
            }
        )

    legend_seen = {}
    for column in columns:
        for stack in (column["yours"], column["cohort"]):
            for segment in stack["segments"]:
                legend_seen.setdefault(segment["label"], segment["color"])
    legend = [
        {"label": label, "color": color}
        for label, color in sorted(legend_seen.items(), key=lambda pair: pair[0].lower())
    ]
    return {"columns": columns, "column_max": column_max, "legend": legend, "mode": mode}


def relationship_participant_stacks(question):
    """Per-participant 'yours' stacks for the research preview overlay.

    Returns ``{participant_id: {likert_key: {"segments": [...], "total": int}}}`` for
    every participant who answered ``question``. The research plot view sends this to
    the browser so a researcher can overlay any single respondent on the cohort chart.
    Never sent to students — it identifies individuals.
    """
    colors_by_value = relationship_color_map(question)
    participant_ids = (
        Response.objects.filter(question=question)
        .values_list("participant__participant_id", flat=True)
        .distinct()
    )
    stacks = {}
    for participant_id in sorted(set(participant_ids)):
        qs = Response.objects.filter(
            question=question,
            participant__participant_id=participant_id,
        )
        stacks[participant_id] = _build_relationship_stack(qs, question, colors_by_value)
    return stacks


def participant_count_for_question(question):
    return (
        Response.objects.filter(question=question)
        .values("participant_id")
        .distinct()
        .count()
    )


def mvp_activities():
    return Activity.objects.filter(slug__in=[RELATIONSHIP_SLUG, HOUSING_SLUG]).order_by("slug")

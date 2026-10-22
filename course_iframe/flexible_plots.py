"""Cohort plots over question fields, without assuming a subject or activity format."""

from collections import Counter
from decimal import Decimal, ROUND_CEILING
import math
import hashlib
import json
import re
import random

from django import forms

from .flexible import (
    NUMERIC_TYPES,
    NUMERIC_LIMITS,
    CHOICE_TYPES,
    answer_label,
    decoded_entry,
    schema_for_question,
)
from .models import Response

COLORS = [
    "#0072b2",
    "#e69f00",
    "#009e73",
    "#cc79a7",
    "#56b4e9",
    "#d55e00",
    "#882255",
    "#7a7a7a",
]


def field_catalog(question):
    result = {}
    for section in schema_for_question(question)["sections"]:
        for field in section["fields"]:
            result[field["id"]] = {
                **field,
                "section_id": section["id"],
                "section_title": section["title"],
            }
    return result


PLOT_TYPES = [
    ("auto", "Automatic overview"),
    ("distribution", "Answer distribution"),
    ("scatter", "Scatter plot"),
    ("stacked", "Category × Likert comparison"),
    ("word_cloud", "Word cloud"),
]
WORD_TYPES = {"text", "long_text", "choice", "multiple"}


class PlotSettingsForm(forms.Form):
    section = forms.ChoiceField(label="Section", required=False)
    chart_type = forms.ChoiceField(
        label="Chart type", choices=PLOT_TYPES, required=False
    )
    question = forms.ChoiceField(label="Question", required=False)
    x = forms.ChoiceField(label="X axis", required=False)
    x_denominator = forms.ChoiceField(label="Divide X by (optional)", required=False)
    y = forms.ChoiceField(label="Y axis", required=False)
    y_denominator = forms.ChoiceField(label="Divide Y by (optional)", required=False)
    category = forms.ChoiceField(label="Categories for stacked chart", required=False)
    scale = forms.ChoiceField(label="Likert question for stacked chart", required=False)

    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog = field_catalog(question)
        self.sections = schema_for_question(question)["sections"]
        self.fields["section"].choices = [("", "All sections")] + [
            (s["id"], s["title"] or f"Section {i + 1}")
            for i, s in enumerate(self.sections)
        ]
        self.fields["question"].choices = [("", "All compatible questions")] + [
            (key, spec["label"]) for key, spec in self.catalog.items()
        ]
        for name in ("x", "x_denominator", "y", "y_denominator", "category", "scale"):
            kinds = (
                NUMERIC_TYPES
                if name in ("x", "x_denominator", "y", "y_denominator")
                else ({"choice"} if name == "category" else {"likert"})
            )
            self.fields[name].choices = [("", "None")] + [
                (
                    key,
                    (spec["section_title"] + " · " if spec["section_title"] else "")
                    + spec["label"],
                )
                for key, spec in self.catalog.items()
                if spec["type"] in kinds
            ]

    def clean(self):
        cleaned = super().clean()
        kind = cleaned["chart_type"] = cleaned.get("chart_type") or "auto"
        section = cleaned.get("section")
        focus = cleaned.get("question")
        if kind in {"scatter", "stacked"}:
            cleaned["question"] = ""
        relevant = ("x", "x_denominator", "y", "y_denominator", "category", "scale")
        for key in relevant:
            if (
                (kind in {"distribution", "word_cloud"})
                or (kind == "scatter" and key in {"category", "scale"})
                or (
                    kind == "stacked"
                    and key in {"x", "x_denominator", "y", "y_denominator"}
                )
            ):
                cleaned[key] = ""
            spec = self.catalog.get(cleaned.get(key))
            if spec and section and spec["section_id"] != section:
                self.add_error(key, "Choose a question in the selected section.")
        if focus in self.catalog and kind not in {"scatter", "stacked"}:
            spec = self.catalog[focus]
            allowed = (
                WORD_TYPES
                if kind == "word_cloud"
                else (
                    CHOICE_TYPES | NUMERIC_TYPES
                    if kind == "distribution"
                    else set(f["type"] for f in self.catalog.values())
                )
            )
            if spec["type"] not in allowed or (
                section and spec["section_id"] != section
            ):
                self.add_error(
                    "question",
                    "Choose a question compatible with this section and chart type.",
                )
        eligible = [
            f
            for f in self.catalog.values()
            if not section or f["section_id"] == section
        ]
        if kind == "distribution" and not any(
            f["type"] in (CHOICE_TYPES | NUMERIC_TYPES) for f in eligible
        ):
            self.add_error(
                "chart_type",
                "Add a choice, Likert, or number question for a distribution.",
            )
        if kind == "word_cloud" and not any(f["type"] in WORD_TYPES for f in eligible):
            self.add_error(
                "chart_type", "Add a text or choice question for a word cloud."
            )
        if kind == "scatter" and (not cleaned.get("x") or not cleaned.get("y")):
            self.add_error("x", "Choose two numeric questions for the scatter plot.")
        if kind == "stacked" and (
            not cleaned.get("category") or not cleaned.get("scale")
        ):
            self.add_error(
                "category",
                "Choose a single-choice question and a Likert question in the same section.",
            )
        for keys, message in [
            (
                ("x", "x_denominator", "y", "y_denominator"),
                "Scatter plot axes must come from the same section so each point represents one entry.",
            ),
            (
                ("category", "scale"),
                "Choose a category and Likert question from the same section.",
            ),
        ]:
            selected = [
                self.catalog[k]
                for name in keys
                if (k := cleaned.get(name)) in self.catalog
            ]
            if len({spec["section_id"] for spec in selected}) > 1:
                raise forms.ValidationError(message)
        if (
            cleaned.get("x")
            and cleaned.get("x") == cleaned.get("y")
            and cleaned.get("x_denominator") == cleaned.get("y_denominator")
        ):
            raise forms.ValidationError(
                "Choose two different numeric questions or ratios for the scatter plot."
            )
        if bool(cleaned.get("x")) != bool(cleaned.get("y")):
            raise forms.ValidationError(
                "Choose both scatter plot axes, or set both to None."
            )
        if bool(cleaned.get("category")) != bool(cleaned.get("scale")):
            raise forms.ValidationError(
                "Choose both questions for the stacked chart, or set both to None."
            )
        return cleaned


def default_plot_settings(question):
    catalog = field_catalog(question)
    config = {
        key: ""
        for key in ["x", "x_denominator", "y", "y_denominator", "category", "scale"]
    }
    for section in schema_for_question(question)["sections"]:
        numeric = [f["id"] for f in section["fields"] if f["type"] in NUMERIC_TYPES]
        categories = [f["id"] for f in section["fields"] if f["type"] == "choice"]
        scales = [f["id"] for f in section["fields"] if f["type"] == "likert"]
        if len(numeric) >= 2 and not config["x"]:
            config.update(x=numeric[0], y=numeric[1])
        if categories and scales and not config["category"]:
            config.update(category=categories[0], scale=scales[0])
    if question.activity.slug == "housing" and {"rooms", "monthly_rent"}.issubset(
        catalog
    ):
        config.update(x="rooms", y="monthly_rent")
    legacy = question.options_json.get("viz", {})
    if isinstance(legacy, dict):
        for name in ["x", "y"]:
            axis = legacy.get(name + "_axis", {})
            if isinstance(axis, str):
                axis = {"numerator": axis}
            if isinstance(axis, dict) and axis.get("numerator") in catalog:
                config[name] = axis["numerator"]
                config[name + "_denominator"] = axis.get("denominator") or ""
    saved = question.options_json.get("visualizations", {})
    if isinstance(saved, dict):
        for key in config:
            if key in saved:
                config[key] = saved[key] if saved[key] in catalog else ""
    for key, value in config.items():
        kinds = (
            NUMERIC_TYPES
            if key in {"x", "y", "x_denominator", "y_denominator"}
            else ({"choice"} if key == "category" else {"likert"})
        )
        if value not in catalog or catalog[value]["type"] not in kinds:
            config[key] = ""
    for keys in [("x", "y", "x_denominator", "y_denominator"), ("category", "scale")]:
        selected = [catalog[config[key]] for key in keys if config[key]]
        if len({spec["section_id"] for spec in selected}) > 1 or bool(
            config[keys[0]]
        ) != bool(config[keys[1]]):
            for key in keys:
                config[key] = ""
    sections = {section["id"] for section in schema_for_question(question)["sections"]}
    config["chart_type"] = (
        saved.get("chart_type", "auto")
        if saved.get("chart_type", "auto") in dict(PLOT_TYPES)
        else "auto"
    )
    config["section"] = (
        saved.get("section", "") if saved.get("section") in sections else ""
    )
    config["question"] = (
        saved.get("question", "") if saved.get("question") in catalog else ""
    )
    return config


def numeric_value(raw):
    try:
        value = float(raw)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def synthetic_entries(question):
    """Build deterministic examples from definitions, without reading or writing responses."""
    rng = random.Random(f"activity-preview:{question.pk}:{question.version}")
    entries = []
    examples = [
        "Clear instructions and useful examples",
        "More practice and feedback",
        "Helpful examples and discussion",
        "Useful feedback and clear explanations",
    ]
    sections = schema_for_question(question)["sections"]
    for learner in range(12):
        for section in sections:
            for _ in range(rng.randint(1, 3) if section["repeatable"] else 1):
                answers = {}
                for spec in section["fields"]:
                    kind = spec["type"]
                    options = spec.get("choices", [])
                    if kind in CHOICE_TYPES:
                        if not options:
                            continue
                        if kind == "multiple":
                            answers[spec["id"]] = [
                                option["value"]
                                for option in rng.sample(
                                    options, min(len(options), rng.randint(1, 2))
                                )
                            ]
                        else:
                            answers[spec["id"]] = rng.choice(options)["value"]
                    elif kind in NUMERIC_TYPES:
                        minimum = Decimal(spec.get("min_value") or "0")
                        value = minimum + Decimal(
                            str((learner + 1) * rng.uniform(1, 4))
                        )
                        if kind == "currency_eur":
                            value += Decimal(str(rng.uniform(50, 400)))
                        step = Decimal(
                            "1"
                            if kind == "integer"
                            else "0.01"
                            if kind == "currency_eur"
                            else "0.000001"
                        )
                        value = min(
                            NUMERIC_LIMITS[kind],
                            value.quantize(step, rounding=ROUND_CEILING),
                        )
                        answers[spec["id"]] = (
                            int(value) if kind == "integer" else str(value)
                        )
                    elif kind in {"text", "long_text"}:
                        answers[spec["id"]] = rng.choice(examples)
                    elif kind == "place":
                        answers[spec["id"]] = f"Example location {learner % 4 + 1}"
                entries.append((section["id"], answers, False))
    return entries, set(range(12)) if sections else set()


def _plot_entries(question, participant=None, *, synthetic=False):
    if synthetic:
        return synthetic_entries(question)
    entries, participants = [], set()
    responses = Response.objects.filter(
        activity_id=question.activity_id
    ).select_related("question", "question__activity")
    if "activity_schema" not in question.options_json:
        responses = responses.filter(question=question)
    catalog = field_catalog(question)
    catalogs = {question.pk: catalog}
    decoded, latest = [], {}
    for response in responses:
        section, answers = decoded_entry(response)
        key = (response.participant_id, section)
        latest[key] = max(latest.get(key, 0), response.question.version)
        decoded.append((response, section, answers))
    for response, section, answers in decoded:
        if response.question.version != latest[(response.participant_id, section)]:
            continue
        if response.question_id not in catalogs:
            catalogs[response.question_id] = field_catalog(response.question)
        original = catalogs[response.question_id]
        compatible = {}
        for key, value in answers.items():
            current, previous = catalog.get(key), original.get(key)
            if not current or not previous or section != current["section_id"]:
                continue
            if all(
                current.get(attr) == previous.get(attr)
                for attr in (
                    "version",
                    "type",
                    "label",
                    "choices",
                    "allow_other",
                    "min_value",
                )
            ):
                compatible[key] = value
        if any(value not in (None, "", []) for value in compatible.values()):
            entries.append(
                (
                    section,
                    compatible,
                    bool(participant and response.participant_id == participant.pk),
                )
            )
            participants.add(response.participant_id)
    return entries, participants


def _single_plot_context(question, *, settings, entries_data):
    catalog = field_catalog(question)
    config = settings
    entries, participants = entries_data
    distributions, numeric_summaries, word_clouds = [], [], []
    for key, spec in catalog.items():
        # Custom comparisons should scan entries once, without recomputing every
        # unrelated question's distributions and word clouds for each saved chart.
        if config["chart_type"] in {"scatter", "stacked"}:
            continue
        if config["question"] and key != config["question"]:
            continue
        if config["section"] and spec["section_id"] != config["section"]:
            continue
        if config["chart_type"] == "word_cloud" and spec["type"] not in WORD_TYPES:
            continue
        raw_values = [
            (answers.get(key), mine)
            for section, answers, mine in entries
            if section == spec["section_id"]
        ]
        if spec["type"] in CHOICE_TYPES:
            counts, yours = Counter(), Counter()
            for value, mine in raw_values:
                values = value if isinstance(value, list) else [value]
                for selected in values:
                    if selected not in (None, "", []):
                        label = answer_label(spec, selected)
                        counts[label] += 1
                        if mine:
                            yours[label] += 1
            labels = [choice["label"] for choice in spec.get("choices", [])]
            labels += [label for label in counts if label not in labels]
            maximum = max(counts.values(), default=1) or 1
            distributions.append(
                {
                    "field_id": key,
                    "title": spec["label"],
                    "section": spec["section_title"],
                    "total": sum(counts.values()),
                    "rows": [
                        {
                            "label": label,
                            "count": counts[label],
                            "yours": yours[label],
                            "percent": round(counts[label] / maximum * 100, 2),
                        }
                        for label in labels
                    ],
                }
            )
            if spec["type"] in {"choice", "multiple"}:
                word_clouds.append(
                    {
                        "field_id": key,
                        "title": spec["label"],
                        "words": [
                            {
                                "label": label,
                                "count": count,
                                "size": round(1 + 1.8 * count / maximum, 2),
                            }
                            for label, count in counts.most_common(30)
                        ],
                    }
                )
        elif spec["type"] in NUMERIC_TYPES:
            values = [
                n for raw, _ in raw_values if (n := numeric_value(raw)) is not None
            ]
            if values:
                low, high = min(values), max(values)
                buckets = Counter()
                for value in values:
                    index = (
                        min(5, int((value - low) / (high - low) * 6))
                        if high != low
                        else 0
                    )
                    buckets[index] += 1
                maximum = max(buckets.values())
                rows = []
                for index in range(6 if high != low else 1):
                    left, right = (
                        low + (high - low) * index / 6,
                        low + (high - low) * (index + 1) / 6,
                    )
                    rows.append(
                        {
                            "label": f"{left:g}–{right:g}"
                            if high != low
                            else f"{low:g}",
                            "count": buckets[index],
                            "percent": round(buckets[index] / maximum * 100, 2),
                        }
                    )
                numeric_summaries.append(
                    {
                        "field_id": key,
                        "title": spec["label"],
                        "count": len(values),
                        "minimum": f"{low:g}",
                        "maximum": f"{high:g}",
                        "mean": f"{sum(values) / len(values):.2f}",
                        "rows": rows,
                    }
                )
            else:
                numeric_summaries.append(
                    {
                        "field_id": key,
                        "title": spec["label"],
                        "count": 0,
                        "minimum": "—",
                        "maximum": "—",
                        "mean": "—",
                        "rows": [],
                    }
                )
        elif spec["type"] in {"text", "long_text"}:
            counts = Counter()
            for value, _ in raw_values:
                if isinstance(value, str):
                    counts.update(
                        word.casefold()
                        for word in re.findall(r"[^\W\d_]{3,}", value, flags=re.UNICODE)
                        if word.casefold()
                        not in {
                            "the",
                            "and",
                            "that",
                            "this",
                            "with",
                            "for",
                            "from",
                            "are",
                            "was",
                            "have",
                            "but",
                            "not",
                        }
                    )
            maximum = max(counts.values(), default=1)
            word_clouds.append(
                {
                    "field_id": key,
                    "title": spec["label"],
                    "answer_count": sum(bool(value) for value, _ in raw_values),
                    "words": [
                        {
                            "label": word,
                            "count": count,
                            "size": round(1 + 1.8 * count / maximum, 2),
                        }
                        for word, count in counts.most_common(30)
                    ],
                }
            )
    points = []

    def axis_value(answers, numerator, denominator):
        value = numeric_value(answers.get(numerator))
        if denominator:
            bottom = numeric_value(answers.get(denominator))
            return (
                value / bottom
                if value is not None and bottom not in (None, 0)
                else None
            )
        return value

    if config["x"] and config["y"]:
        for section, answers, mine in entries:
            if section != catalog[config["x"]]["section_id"]:
                continue
            x = axis_value(answers, config["x"], config["x_denominator"])
            y = axis_value(answers, config["y"], config["y_denominator"])
            if (
                x is not None
                and y is not None
                and math.isfinite(x)
                and math.isfinite(y)
            ):
                points.append({"x": x, "y": y, "is_yours": mine})

    def axis_label(key):
        name = catalog.get(config[key], {}).get("label", "")
        denominator = config[key + "_denominator"]
        return name + (" ÷ " + catalog[denominator]["label"] if denominator else "")

    stacked = None
    if config["category"] and config["scale"]:
        category, scale = catalog[config["category"]], catalog[config["scale"]]
        counts = Counter()
        for section, answers, _ in entries:
            if (
                section == category["section_id"]
                and answers.get(config["category"]) not in (None, "")
                and answers.get(config["scale"]) not in (None, "")
            ):
                counts[
                    (
                        answer_label(scale, answers[config["scale"]]),
                        answer_label(category, answers[config["category"]]),
                    )
                ] += 1
        categories = list(dict.fromkeys(label for _, label in counts))
        colors = {
            label: COLORS[index % len(COLORS)] for index, label in enumerate(categories)
        }
        rows = []
        for option in scale["choices"]:
            label = option["label"]
            total = sum(counts[(label, c)] for c in categories)
            rows.append(
                {
                    "label": label,
                    "total": total,
                    "segments": [
                        {
                            "label": c,
                            "count": counts[(label, c)],
                            "percent": round(counts[(label, c)] / total * 100, 3)
                            if total
                            else 0,
                            "color": colors[c],
                        }
                        for c in categories
                    ],
                }
            )
        stacked = {
            "title": scale["label"] + " by " + category["label"].lower(),
            "rows": rows,
            "legend": [{"label": c, "color": colors[c]} for c in categories],
        }

    def selected(chart):
        key = chart["field_id"]
        return (
            not config["section"] or catalog[key]["section_id"] == config["section"]
        ) and (not config["question"] or key == config["question"])

    distributions = [chart for chart in distributions if selected(chart)]
    numeric_summaries = [chart for chart in numeric_summaries if selected(chart)]
    word_clouds = [chart for chart in word_clouds if selected(chart)]
    scatter_enabled = bool(config["x"] and config["y"] and not config["question"])
    if (
        config["section"]
        and config["x"]
        and catalog[config["x"]]["section_id"] != config["section"]
    ):
        scatter_enabled = False
    if config["question"] or (
        config["section"]
        and config["category"]
        and catalog[config["category"]]["section_id"] != config["section"]
    ):
        stacked = None
    kind = config["chart_type"]
    if kind != "auto":
        if kind != "distribution":
            distributions, numeric_summaries = [], []
        if kind != "word_cloud":
            word_clouds = []
        if kind != "scatter":
            scatter_enabled = False
        if kind != "stacked":
            stacked = None
    if not scatter_enabled:
        points = []
    return {
        "distributions": distributions,
        "numeric_summaries": numeric_summaries,
        "word_clouds": word_clouds,
        "scatter_points": points,
        "scatter_enabled": scatter_enabled,
        "x_label": axis_label("x"),
        "y_label": axis_label("y"),
        "stacked": stacked,
        "response_count": len(entries),
        "participant_count": len(participants),
        "plot_settings": config,
    }


def _chart_cards(context):
    """Stable identities let visibility survive new data and question label changes."""
    settings = context["plot_settings"]
    cards = []

    def add(kind, title, data, identity):
        key = hashlib.sha256(
            json.dumps([kind, identity], sort_keys=True).encode()
        ).hexdigest()[:20]
        cards.append({"id": key, "kind": kind, "title": title, "data": data})

    if context["scatter_enabled"]:
        add(
            "scatter",
            context["y_label"] + " vs " + context["x_label"],
            {
                "points": context["scatter_points"],
                "x_label": context["x_label"],
                "y_label": context["y_label"],
            },
            [settings[key] for key in ("x", "x_denominator", "y", "y_denominator")],
        )
    if context["stacked"]:
        add(
            "stacked",
            context["stacked"]["title"],
            context["stacked"],
            [settings["category"], settings["scale"]],
        )
    for kind, key in [
        ("distribution", "distributions"),
        ("numeric", "numeric_summaries"),
        ("word_cloud", "word_clouds"),
    ]:
        for chart in context[key]:
            add(kind, chart["title"], chart, chart["field_id"])
    return cards


def plot_context(
    question,
    participant=None,
    *,
    synthetic=False,
    researcher=False,
    include_library=True,
):
    entries_data = _plot_entries(question, participant, synthetic=synthetic)
    context = _single_plot_context(
        question, settings=default_plot_settings(question), entries_data=entries_data
    )
    cards = _chart_cards(context)
    if include_library:
        for settings in question.options_json.get("chart_library", []):
            form = PlotSettingsForm(settings, question=question)
            # A removed or changed question must never silently become a different chart.
            if form.is_valid():
                cards.extend(
                    _chart_cards(
                        _single_plot_context(
                            question,
                            settings=form.cleaned_data,
                            entries_data=entries_data,
                        )
                    )
                )
    unique = {card["id"]: card for card in cards}
    selected = question.options_json.get("student_chart_ids")
    for card in unique.values():
        card["student_visible"] = selected is None or card["id"] in selected
        card["data_id"] = "scatter-" + card["id"]
    context["plot_cards"] = [
        card
        for card in unique.values()
        if researcher or not include_library or card["student_visible"]
    ]
    return context

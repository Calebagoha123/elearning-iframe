from decimal import Decimal
import random

from django.core.management.base import BaseCommand
from django.db import transaction

from course_iframe.models import Participant
from course_iframe.services import (
    HOUSING_FIELD_CHOICE,
    HOUSING_FIELD_CURRENCY_EUR,
    HOUSING_FIELD_DECIMAL,
    HOUSING_FIELD_INTEGER,
    HOUSING_FIELD_PLACE,
    HOUSING_QUESTION_SLUG,
    HOUSING_SLUG,
    RELATIONSHIP_QUESTION_SLUG,
    RELATIONSHIP_SLUG,
    active_question,
    get_or_create_participant,
    housing_schema,
    relationship_likert_choices,
    save_housing_response,
    save_relationship_responses,
)


DISTRICTS = [
    ("Berlin Kreuzberg", "52.4990", "13.4180", Decimal("1.12")),
    ("Berlin Neukolln", "52.4810", "13.4350", Decimal("0.98")),
    ("Berlin Wedding", "52.5500", "13.3500", Decimal("0.88")),
    ("Berlin Prenzlauer Berg", "52.5390", "13.4240", Decimal("1.22")),
    ("Berlin Charlottenburg", "52.5160", "13.3040", Decimal("1.16")),
    ("Berlin Friedrichshain", "52.5150", "13.4540", Decimal("1.08")),
    ("Berlin Moabit", "52.5300", "13.3440", Decimal("0.94")),
    ("Berlin Schoneberg", "52.4820", "13.3490", Decimal("1.05")),
]

RELATIONSHIP_FALLBACKS = [
    {"value": "friend", "label": "Friend"},
    {"value": "parent", "label": "Parent"},
    {"value": "sibling", "label": "Sibling"},
    {"value": "colleague", "label": "Colleague"},
    {"value": "boss", "label": "Boss"},
    {"value": "landlord", "label": "Landlord"},
]


class Command(BaseCommand):
    help = "Seed deterministic synthetic responses for demo/test visualizations and CSV export."

    def add_arguments(self, parser):
        parser.add_argument("--participants", type=int, default=36)
        parser.add_argument("--housing-entries", type=int, default=90)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Keep existing demo-* participants instead of replacing them.",
        )

    def handle(self, *args, **options):
        participant_count = max(1, options["participants"])
        housing_entry_count = max(1, options["housing_entries"])
        rng = random.Random(options["seed"])

        relationship_question = active_question(RELATIONSHIP_SLUG, RELATIONSHIP_QUESTION_SLUG)
        housing_question = active_question(HOUSING_SLUG, HOUSING_QUESTION_SLUG)

        with transaction.atomic():
            if not options["keep_existing"]:
                Participant.objects.filter(participant_id__startswith="demo-").delete()

            participants = [
                get_or_create_participant(f"demo-{index:03d}")
                for index in range(1, participant_count + 1)
            ]

            relationship_rows = self.seed_relationships(
                participants,
                relationship_question,
                rng,
            )
            housing_rows = self.seed_housing(
                participants,
                housing_question,
                housing_entry_count,
                rng,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(participants)} demo participants, "
                f"{relationship_rows} relationship responses, "
                f"and {housing_rows} housing entries."
            )
        )

    def seed_relationships(self, participants, question, rng):
        choices = (question.options_json or {}).get("choices") or RELATIONSHIP_FALLBACKS
        income_choices = relationship_likert_choices(question)
        total = 0
        for participant in participants:
            count = rng.choice([1, 1, 2, 2, 3])
            selected = rng.sample(choices, k=min(count, len(choices)))
            payloads = []
            for choice in selected:
                income_value, income_label = rng.choices(
                    income_choices,
                    weights=self.income_weights(income_choices, choice.get("value", "")),
                    k=1,
                )[0]
                payloads.append(
                    {
                        "relationship_value": choice["value"],
                        "relationship_label": choice["label"],
                        "income_comparison_value": income_value,
                        "income_comparison_label": income_label,
                    }
                )
            save_relationship_responses(participant, question, payloads)
            total += len(payloads)
        return total

    def income_weights(self, income_choices, relationship_value):
        values = [value for value, _label in income_choices]
        weights = []
        for value in values:
            if relationship_value in {"boss", "landlord", "parent"} and "higher" in value:
                weights.append(5)
            elif relationship_value in {"sibling", "friend", "colleague"} and value == "same":
                weights.append(4)
            elif relationship_value in {"student", "child"} and "lower" in value:
                weights.append(4)
            else:
                weights.append(2)
        return weights

    def seed_housing(self, participants, question, entry_count, rng):
        for index in range(entry_count):
            participant = participants[index % len(participants)]
            district, lat, lon, multiplier = rng.choice(DISTRICTS)
            rooms = rng.choice([Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("2.5"), Decimal("3"), Decimal("4")])
            size = (rooms * Decimal(str(rng.randint(24, 33))) + Decimal(str(rng.randint(-5, 10)))).quantize(Decimal("0.1"))
            base_rent = Decimal("420") + (rooms * Decimal("285")) + (size * Decimal("8.75"))
            monthly_rent = (base_rent * multiplier * Decimal(str(rng.uniform(0.88, 1.18)))).quantize(Decimal("0.01"))

            cleaned = {}
            for field in housing_schema(question):
                key = field["key"]
                ftype = field["type"]
                if ftype == HOUSING_FIELD_PLACE:
                    cleaned[key] = district
                    cleaned[f"{key}_meta"] = {
                        "display_name": district,
                        "osm_type": "relation",
                        "osm_id": str(10_000 + index),
                        "lat": lat,
                        "lon": lon,
                    }
                elif ftype == HOUSING_FIELD_CURRENCY_EUR:
                    cleaned[key] = monthly_rent if key == "monthly_rent" else self.random_money(rng)
                elif ftype == HOUSING_FIELD_DECIMAL:
                    cleaned[key] = self.decimal_for_key(key, rooms, size, rng)
                elif ftype == HOUSING_FIELD_INTEGER:
                    cleaned[key] = self.integer_for_key(key, rooms, rng)
                elif ftype == HOUSING_FIELD_CHOICE:
                    options = field.get("options") or []
                    cleaned[key] = rng.choice(options)["value"] if options else ""
                else:
                    cleaned[key] = f"Demo entry {index + 1}"
            save_housing_response(participant, question, cleaned)
        return entry_count

    def decimal_for_key(self, key, rooms, size, rng):
        key_lower = key.lower()
        if "room" in key_lower:
            return rooms
        if "size" in key_lower or "sqm" in key_lower or "area" in key_lower:
            return size
        return Decimal(str(rng.randint(1, 100))).quantize(Decimal("0.1"))

    def integer_for_key(self, key, rooms, rng):
        key_lower = key.lower()
        if "room" in key_lower:
            return int(rooms)
        if "year" in key_lower:
            return rng.randint(1890, 2024)
        return rng.randint(1, 12)

    def random_money(self, rng):
        return Decimal(str(rng.randint(350, 2400))).quantize(Decimal("0.01"))

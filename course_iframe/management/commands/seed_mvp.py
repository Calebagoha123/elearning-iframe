from django.core.management.base import BaseCommand

from course_iframe.models import Activity, Question


RELATIONSHIP_CHOICES = [
    {"value": "neighbour", "label": "Neighbour"},
    {"value": "friend", "label": "Friend"},
    {"value": "boss", "label": "Boss"},
    {"value": "parent", "label": "Parent"},
    {"value": "sibling", "label": "Sibling"},
    {"value": "partner", "label": "Partner"},
    {"value": "colleague", "label": "Colleague"},
    {"value": "teacher", "label": "Teacher"},
    {"value": "landlord", "label": "Landlord"},
]


class Command(BaseCommand):
    help = "Create or update the default MVP activities and questions."

    def handle(self, *args, **options):
        relationship, _ = Activity.objects.update_or_create(
            slug="relationship",
            defaults={
                "title": "Relationship survey",
                "activity_type": "relationship_survey",
                "is_active": True,
            },
        )
        Question.objects.update_or_create(
            activity=relationship,
            slug="relationship_type",
            version=1,
            defaults={
                "question_text": "Think of someone whose income you know roughly. What is your relationship to them?",
                "field_type": "choice",
                "options_json": {"choices": RELATIONSHIP_CHOICES, "likert_points": 5},
                "is_active": True,
            },
        )
        housing, _ = Activity.objects.update_or_create(
            slug="housing",
            defaults={
                "title": "Housing data entry",
                "activity_type": "housing_survey",
                "is_active": True,
            },
        )
        Question.objects.update_or_create(
            activity=housing,
            slug="housing_property",
            version=1,
            defaults={
                "question_text": "Enter one researched rental property from your local housing market.",
                "field_type": "housing",
                "options_json": {},
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS("Seeded MVP activities and questions."))

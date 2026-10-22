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
    help = "Create missing default MVP activities and questions."

    def ensure_seed_question(self, activity, slug, defaults):
        questions = Question.objects.filter(activity=activity, slug=slug)
        if not questions.exists():
            Question.objects.create(
                activity=activity,
                slug=slug,
                version=1,
                **defaults,
            )
            return

        active_questions = questions.filter(is_active=True).order_by("-version", "-id")
        keep = active_questions.first()
        if keep is None:
            keep = questions.order_by("-version", "-id").first()
            Question.objects.filter(pk=keep.pk).update(is_active=True)
            return
        active_questions.exclude(pk=keep.pk).update(is_active=False)

    def handle(self, *args, **options):
        relationship, _ = Activity.objects.get_or_create(
            slug="relationship",
            defaults={
                "title": "Relationship survey",
                "activity_type": "relationship_survey",
                "is_active": True,
            },
        )
        self.ensure_seed_question(
            relationship,
            "relationship_type",
            {
                "question_text": "Think of someone whose income you know roughly. What is your relationship to them?",
                "field_type": "choice",
                "options_json": {"choices": RELATIONSHIP_CHOICES, "likert_points": 5},
                "is_active": True,
            },
        )
        housing, _ = Activity.objects.get_or_create(
            slug="housing",
            defaults={
                "title": "Housing data entry",
                "activity_type": "housing_survey",
                "is_active": True,
            },
        )
        self.ensure_seed_question(
            housing,
            "housing_property",
            {
                "question_text": "Enter one researched rental property from your local housing market.",
                "field_type": "housing",
                "options_json": {},
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS("Seeded MVP activities and questions."))

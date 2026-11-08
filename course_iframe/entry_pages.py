"""Per-activity entry-page copy and language defaults."""

from django.shortcuts import render

from .forms import ParticipantEntryForm

ENTRY_LANGUAGES = [("en", "English"), ("de", "Deutsch"), ("nl", "Nederlands")]
ENTRY_DEFAULTS = {
    "en": {
        "eyebrow": "Activity",
        "label": "Pseudoname",
        "help": "Use the pseudoname shown in Moodle.",
        "button": "Start activity",
        "required": "Enter your Moodle pseudoname.",
        "too_long": "Use no more than 160 characters.",
        "privacy": "Your responses will be collected and aggregated with data from other activities for research purposes. Although stored under a pseudonym, combining responses across activities may make it possible to re-identify individual participants from the pseudonymized data.",
    },
    "de": {
        "eyebrow": "Aktivität",
        "label": "Pseudonym",
        "help": "Verwenden Sie das in Moodle angegebene Pseudonym.",
        "button": "Aktivität starten",
        "required": "Bitte geben Sie Ihr Pseudonym ein.",
        "too_long": "Verwenden Sie höchstens 160 Zeichen.",
        "privacy": "Ihre Antworten werden zu Forschungszwecken erfasst und mit Daten aus anderen Aktivitäten zusammengeführt. Obwohl die Daten unter einem Pseudonym gespeichert werden, kann die Zusammenführung eine erneute Identifizierung einzelner Teilnehmender ermöglichen.",
    },
    "nl": {
        "eyebrow": "Activiteit",
        "label": "Pseudoniem",
        "help": "Gebruik het pseudoniem dat in Moodle wordt weergegeven.",
        "button": "Activiteit starten",
        "required": "Vul uw pseudoniem in.",
        "too_long": "Gebruik maximaal 160 tekens.",
        "privacy": "Uw antwoorden worden voor onderzoeksdoeleinden verzameld en samengevoegd met gegevens uit andere activiteiten. Hoewel de gegevens onder een pseudoniem worden opgeslagen, kan het combineren van antwoorden het mogelijk maken individuele deelnemers opnieuw te identificeren.",
    },
}
ENTRY_COPY_FIELDS = (
    "heading",
    "intro",
    "eyebrow",
    "label",
    "help",
    "privacy",
    "button",
)


def entry_configuration(activity=None, title=""):
    settings = activity.entry_settings if activity else {}
    language = settings.get("language", "en")
    if language not in ENTRY_DEFAULTS:
        language = "en"
    defaults = {
        "heading": activity.title if activity else title,
        "intro": "",
        **ENTRY_DEFAULTS[language],
    }
    return {
        **defaults,
        **{key: settings[key] for key in ENTRY_COPY_FIELDS if key in settings},
        "language": language,
        "skip": bool(settings.get("skip", False)),
    }


def entry_form(activity=None, data=None, initial=None):
    copy = entry_configuration(activity)
    form = ParticipantEntryForm(data, initial=initial)
    field = form.fields["pseudoname"]
    field.label = copy["label"] or ENTRY_DEFAULTS[copy["language"]]["label"]
    field.help_text = copy["help"]
    field.error_messages.update(required=copy["required"], max_length=copy["too_long"])
    return form


def render_entry(request, activity, form=None, *, title="", preview=False):
    copy = entry_configuration(activity, title)
    return render(
        request,
        "course_iframe/participant_entry.html",
        {
            "activity_title": activity.title if activity else title,
            "entry": copy,
            "page_language": copy["language"],
            "form": form if form is not None else entry_form(activity),
            "entry_preview": preview,
            "activity": activity,
        },
    )

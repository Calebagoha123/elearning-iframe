"""Author-written language variants over stable question and answer identifiers."""

from copy import copy, deepcopy
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.http import Http404

LANGUAGES = [
    ("en", "English"),
    ("nl", "Nederlands"),
    ("de", "Deutsch"),
    ("fr", "Français"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}
# Fixed interface copy; authors can override these labels for every language.
_ROWS = {
    "save": ("Save", "Opslaan", "Speichern", "Enregistrer"),
    "results": ("Results", "Resultaten", "Ergebnisse", "Résultats"),
    "edit": (
        "Edit answers",
        "Antwoorden bewerken",
        "Antworten bearbeiten",
        "Modifier les réponses",
    ),
    "your_answers": ("Your answers", "Uw antwoorden", "Ihre Antworten", "Vos réponses"),
    "empty_answers": (
        "No answers yet.",
        "Nog geen antwoorden.",
        "Noch keine Antworten.",
        "Aucune réponse pour le moment.",
    ),
    "add_entry": (
        "Add another entry",
        "Nog een item toevoegen",
        "Weiteren Eintrag hinzufügen",
        "Ajouter une entrée",
    ),
    "remove_entry": (
        "Remove entry",
        "Invoer verwijderen",
        "Eintrag entfernen",
        "Supprimer cette entrée",
    ),
    "choose": (
        "Choose an answer",
        "Kies een antwoord",
        "Antwort auswählen",
        "Choisissez une réponse",
    ),
    "other": ("Other", "Anders", "Sonstiges", "Autre"),
    "other_answer": (
        "Other answer",
        "Ander antwoord",
        "Andere Antwort",
        "Autre réponse",
    ),
    "required": (
        "This answer is required.",
        "Dit antwoord is verplicht.",
        "Diese Antwort ist erforderlich.",
        "Cette réponse est obligatoire.",
    ),
    "describe_other": (
        "Describe your other answer.",
        "Beschrijf uw andere antwoord.",
        "Bitte beschreiben Sie Ihre andere Antwort.",
        "Précisez votre autre réponse.",
    ),
    "place_prompt": (
        "Type a location and choose a suggestion.",
        "Typ een locatie en kies een suggestie.",
        "Geben Sie einen Ort ein und wählen Sie einen Vorschlag.",
        "Saisissez un lieu et choisissez une suggestion.",
    ),
    "place_error": (
        "Choose a location from the suggestions.",
        "Kies een locatie uit de suggesties.",
        "Wählen Sie einen Ort aus den Vorschlägen.",
        "Choisissez un lieu parmi les suggestions.",
    ),
    "searching": ("Searching…", "Zoeken…", "Suche läuft…", "Recherche…"),
    "place_selected": (
        "Location selected.",
        "Locatie geselecteerd.",
        "Ort ausgewählt.",
        "Lieu sélectionné.",
    ),
    "choose_place": (
        "Choose a location from the suggestions.",
        "Kies een locatie uit de suggesties.",
        "Wählen Sie einen Ort aus den Vorschlägen.",
        "Choisissez un lieu parmi les suggestions.",
    ),
    "no_places": (
        "No matching locations.",
        "Geen locaties gevonden.",
        "Keine passenden Orte gefunden.",
        "Aucun lieu trouvé.",
    ),
    "search_unavailable": (
        "Location search is unavailable. Try again shortly.",
        "Locatie zoeken is niet beschikbaar. Probeer het later opnieuw.",
        "Die Ortssuche ist nicht verfügbar. Bitte versuchen Sie es später erneut.",
        "La recherche de lieux est indisponible. Réessayez plus tard.",
    ),
    "stale": (
        "This activity has changed. Reload it before saving.",
        "Deze activiteit is gewijzigd. Laad deze opnieuw voordat u opslaat.",
        "Diese Aktivität wurde geändert. Laden Sie sie vor dem Speichern neu.",
        "Cette activité a changé. Rechargez-la avant d’enregistrer.",
    ),
    "empty_questions": (
        "No questions have been added yet.",
        "Er zijn nog geen vragen toegevoegd.",
        "Es wurden noch keine Fragen hinzugefügt.",
        "Aucune question n’a encore été ajoutée.",
    ),
    "no_paired": (
        "No entries with values for both axes yet.",
        "Nog geen invoer met waarden voor beide assen.",
        "Noch keine Einträge mit Werten für beide Achsen.",
        "Aucune entrée avec des valeurs pour les deux axes.",
    ),
    "no_numeric": (
        "No numeric answers yet.",
        "Nog geen numerieke antwoorden.",
        "Noch keine numerischen Antworten.",
        "Aucune réponse numérique pour le moment.",
    ),
    "no_text": (
        "No text answers yet.",
        "Nog geen tekstantwoorden.",
        "Noch keine Textantworten.",
        "Aucune réponse textuelle pour le moment.",
    ),
    "no_charts": (
        "No charts have been shared for this activity yet.",
        "Er zijn nog geen grafieken gedeeld voor deze activiteit.",
        "Für diese Aktivität wurden noch keine Diagramme freigegeben.",
        "Aucun graphique n’a encore été partagé pour cette activité.",
    ),
    "answers": ("answers", "antwoorden", "Antworten", "réponses"),
    "mean": ("Mean", "Gemiddelde", "Mittelwert", "Moyenne"),
    "range": ("Range", "Bereik", "Spannweite", "Étendue"),
    "yours": ("yours", "van u", "Ihre", "les vôtres"),
    "by": ("by", "per", "nach", "selon"),
    "own_legend": (
        "Purple points show the cohort. Gold points show your entries.",
        "Paarse punten tonen de groep. Gouden punten tonen uw invoer.",
        "Violette Punkte zeigen die Gruppe. Goldene Punkte zeigen Ihre Einträge.",
        "Les points violets représentent le groupe. Les points dorés représentent vos entrées.",
    ),
}
UI_COPY = {
    code: {key: texts[index] for key, texts in _ROWS.items()}
    for index, (code, _) in enumerate(LANGUAGES)
}
UI_EDITABLE = (
    "save",
    "results",
    "edit",
    "your_answers",
    "remove_entry",
    "choose",
    "other",
    "other_answer",
)


def semantic(value):
    """Translation additions do not change the meaning or answer codes of a question."""
    if isinstance(value, dict):
        return {
            key: semantic(item) for key, item in value.items() if key != "translations"
        }
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def validate_text_variants(value, allowed):
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or not set(value).issubset(LANGUAGE_CODES):
        raise ValidationError("Choose a supported language for every translation.")
    result = {}
    for code, texts in value.items():
        if not isinstance(texts, dict) or not set(texts).issubset(allowed):
            raise ValidationError("Translation fields are invalid.")
        result[code] = {}
        for key, text in texts.items():
            if not isinstance(text, str) or len(text) > allowed[key]:
                raise ValidationError(
                    f"Keep translated {key} within {allowed[key]} characters."
                )
            result[code][key] = text.strip()
    return result


def validate_activity_variants(value):
    if not isinstance(value, dict) or not set(value).issubset(LANGUAGE_CODES):
        raise ValidationError("Activity language versions are invalid.")
    clean = {}
    for code, text in value.items():
        if not isinstance(text, dict) or not set(text).issubset(
            {"title", "description", "ui"}
        ):
            raise ValidationError("Activity translations are invalid.")
        part = {key: item for key, item in text.items() if key != "ui"}
        clean[code] = validate_text_variants(
            {code: part}, {"title": 160, "description": 10000}
        )[code]
        clean[code]["ui"] = validate_text_variants(
            {code: text.get("ui", {})}, {key: 200 for key in UI_EDITABLE}
        )[code]
    return clean


def translated_schema(schema, language):
    result = deepcopy(schema)
    for section in result["sections"]:
        section.update(
            {
                key: value
                for key, value in section.get("translations", {})
                .get(language, {})
                .items()
                if value
            }
        )
        for field in section["fields"]:
            field.update(
                {
                    key: value
                    for key, value in field.get("translations", {})
                    .get(language, {})
                    .items()
                    if value
                }
            )
            for choice in field.get("choices", []):
                choice.update(
                    {
                        key: value
                        for key, value in choice.get("translations", {})
                        .get(language, {})
                        .items()
                        if value
                    }
                )
    return result


def translated_activity(activity, language):
    result = copy(activity)
    if language != activity.source_language:
        for key in ("title", "description"):
            text = activity.translations.get(language, {}).get(key)
            if text:
                setattr(result, key, text)
    return result


def ui_copy(activity, language):
    return {
        **UI_COPY[language],
        **{
            key: text
            for key, text in activity.translations.get(language, {})
            .get("ui", {})
            .items()
            if text
        },
    }


def missing_translations(activity, schema, language):
    if language == activity.source_language:
        return []
    missing = []
    for key in ("title", "description"):
        if getattr(activity, key) and not activity.translations.get(language, {}).get(
            key
        ):
            missing.append(key)
    for section in schema["sections"]:
        for key in ("title", "instructions", "add_label"):
            if key == "add_label" and not section["repeatable"]:
                continue
            if section.get(key) and not section.get("translations", {}).get(
                language, {}
            ).get(key):
                missing.append(section["id"] + ":" + key)
        for field in section["fields"]:
            for key in ("label", "help_text"):
                if field.get(key) and not field.get("translations", {}).get(
                    language, {}
                ).get(key):
                    missing.append(field["id"] + ":" + key)
            for choice in field.get("choices", []):
                if not choice.get("translations", {}).get(language, {}).get("label"):
                    missing.append(field["id"] + ":" + choice["value"])
    return missing


def language_status(activity, schema):
    codes = [activity.source_language] + [
        code for code in activity.translations if code != activity.source_language
    ]
    return [
        {
            "code": code,
            "name": dict(LANGUAGES)[code],
            "missing": missing_translations(activity, schema, code),
        }
        for code in codes
    ]


def requested_language(request, activity, schema, *, preview=False):
    language = (
        request.GET.get("lang")
        or request.POST.get("language")
        or activity.source_language
    )
    if language not in LANGUAGE_CODES:
        raise Http404("Unknown language version.")
    if language != activity.source_language:
        if language not in activity.translations or (
            not preview and missing_translations(activity, schema, language)
        ):
            raise Http404("This language version is not ready.")
    return language


def language_url(url, language, source_language):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if language == source_language:
        query.pop("lang", None)
    else:
        query["lang"] = language
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )

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
    ("es", "Español"),
    ("pt", "Português"),
    ("it", "Italiano"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}
# Fixed interface copy; authors can override these labels for every language.
_ROWS = {
    "personal_tab": ("You", "U", "Sie", "Vous"),
    "aggregate_tab": ("Everyone", "Iedereen", "Alle", "Tout le monde"),
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
    for index, (code, _) in enumerate(LANGUAGES[:4])
}
UI_COPY.update(
    {
        "es": {
            "personal_tab": "Tú",
            "aggregate_tab": "Todos",
            "save": "Guardar",
            "results": "Resultados",
            "edit": "Editar respuestas",
            "your_answers": "Tus respuestas",
            "empty_answers": "Todavía no hay respuestas.",
            "add_entry": "Añadir otra entrada",
            "remove_entry": "Eliminar entrada",
            "choose": "Elige una respuesta",
            "other": "Otro",
            "other_answer": "Otra respuesta",
            "required": "Esta respuesta es obligatoria.",
            "describe_other": "Describe tu otra respuesta.",
            "place_prompt": "Escribe una ubicación y elige una sugerencia.",
            "place_error": "Elige una ubicación de las sugerencias.",
            "searching": "Buscando…",
            "place_selected": "Ubicación seleccionada.",
            "choose_place": "Elige una ubicación de las sugerencias.",
            "no_places": "No se encontraron ubicaciones.",
            "search_unavailable": "La búsqueda de ubicaciones no está disponible. Inténtalo de "
            "nuevo más tarde.",
            "stale": "Esta actividad ha cambiado. Vuelve a cargarla antes de guardar.",
            "empty_questions": "Todavía no se han añadido preguntas.",
            "no_paired": "Todavía no hay entradas con valores para ambos ejes.",
            "no_numeric": "Todavía no hay respuestas numéricas.",
            "no_text": "Todavía no hay respuestas de texto.",
            "no_charts": "Todavía no se han compartido gráficos para esta actividad.",
            "answers": "respuestas",
            "mean": "Media",
            "range": "Rango",
            "yours": "tuyas",
            "by": "por",
            "own_legend": "Los puntos morados representan al grupo. Los puntos dorados "
            "representan tus entradas.",
        },
        "pt": {
            "personal_tab": "Você",
            "aggregate_tab": "Todos",
            "save": "Guardar",
            "results": "Resultados",
            "edit": "Editar respostas",
            "your_answers": "As suas respostas",
            "empty_answers": "Ainda não há respostas.",
            "add_entry": "Adicionar outra entrada",
            "remove_entry": "Eliminar entrada",
            "choose": "Escolha uma resposta",
            "other": "Outro",
            "other_answer": "Outra resposta",
            "required": "Esta resposta é obrigatória.",
            "describe_other": "Descreva a sua outra resposta.",
            "place_prompt": "Escreva uma localização e escolha uma sugestão.",
            "place_error": "Escolha uma localização nas sugestões.",
            "searching": "A pesquisar…",
            "place_selected": "Localização selecionada.",
            "choose_place": "Escolha uma localização nas sugestões.",
            "no_places": "Não foram encontradas localizações.",
            "search_unavailable": "A pesquisa de localizações está indisponível. Tente novamente "
            "mais tarde.",
            "stale": "Esta atividade foi alterada. Recarregue-a antes de guardar.",
            "empty_questions": "Ainda não foram adicionadas perguntas.",
            "no_paired": "Ainda não há entradas com valores para ambos os eixos.",
            "no_numeric": "Ainda não há respostas numéricas.",
            "no_text": "Ainda não há respostas de texto.",
            "no_charts": "Ainda não foram partilhados gráficos para esta atividade.",
            "answers": "respostas",
            "mean": "Média",
            "range": "Intervalo",
            "yours": "suas",
            "by": "por",
            "own_legend": "Os pontos roxos representam o grupo. Os pontos dourados representam as "
            "suas entradas.",
        },
        "it": {
            "personal_tab": "Tu",
            "aggregate_tab": "Tutti",
            "save": "Salva",
            "results": "Risultati",
            "edit": "Modifica risposte",
            "your_answers": "Le tue risposte",
            "empty_answers": "Non ci sono ancora risposte.",
            "add_entry": "Aggiungi una voce",
            "remove_entry": "Elimina voce",
            "choose": "Scegli una risposta",
            "other": "Altro",
            "other_answer": "Altra risposta",
            "required": "Questa risposta è obbligatoria.",
            "describe_other": "Descrivi la tua altra risposta.",
            "place_prompt": "Digita una località e scegli un suggerimento.",
            "place_error": "Scegli una località tra i suggerimenti.",
            "searching": "Ricerca…",
            "place_selected": "Località selezionata.",
            "choose_place": "Scegli una località tra i suggerimenti.",
            "no_places": "Nessuna località trovata.",
            "search_unavailable": "La ricerca delle località non è disponibile. Riprova tra poco.",
            "stale": "Questa attività è cambiata. Ricaricala prima di salvare.",
            "empty_questions": "Non sono ancora state aggiunte domande.",
            "no_paired": "Non ci sono ancora voci con valori per entrambi gli assi.",
            "no_numeric": "Non ci sono ancora risposte numeriche.",
            "no_text": "Non ci sono ancora risposte di testo.",
            "no_charts": "Non sono ancora stati condivisi grafici per questa attività.",
            "answers": "risposte",
            "mean": "Media",
            "range": "Intervallo",
            "yours": "tue",
            "by": "per",
            "own_legend": "I punti viola rappresentano il gruppo. I punti dorati rappresentano le "
            "tue voci.",
        },
    }
)
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

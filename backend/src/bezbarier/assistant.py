"""Asystent: odpowiada na pytania o miejsca na podstawie danych aplikacji.

Mały model językowy (domyślnie Gemini Flash-Lite) dostaje wyłącznie oceny miejsc policzone dla profilu
użytkownika i ma z nich ułożyć krótką odpowiedź - bez wymyślania miejsc i bez słowa "dostępne".
Bez klucza API (albo gdy model nie odpowie) działa prosta odpowiedź oparta na regułach.

Reklamy (komercjalizacja) są celowo POZA modelem: model nie wie o sponsorach, więc nie może polecać
miejsc za pieniądze. Backend dokłada co najwyżej jedno wyraźnie oznaczone miejsce sponsorowane,
i tylko takie, które dla profilu pytającego nie ma znanych przeszkód.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel

from .storage import Sponsorship

SUMMARY_PL = {
    "no_known_barriers": "brak znanych przeszkód",
    "difficulties": "utrudnienia lub rzeczy do sprawdzenia",
    "incomplete_data": "niepełne dane",
    "barriers": "przeszkody dla Twoich ustawień",
}
TIER = {"no_known_barriers": 0, "difficulties": 1, "incomplete_data": 2, "barriers": 3}

# Kategorie pytań -> słowa w pytaniu i rodzaje obiektów z OSM (Place.kind = "klucz:wartość")
CATEGORIES: dict[str, tuple[list[str], list[str]]] = {
    # rdzenie wyrazów - polska odmiana ("kawa", "kawę", "kawiarnia")
    "jedzenie": (["zjeść", "zjem", "jedzeni", "restaurac", "kaw", "obiad", "śniadani", "kolacj", "bar ", "pizz", "lody"],
                 ["amenity:restaurant", "amenity:cafe", "amenity:fast_food", "amenity:bar", "amenity:pub"]),
    "nocleg": (["hotel", "nocleg", "spać", "hostel", "pensjonat"],
               ["tourism:hotel", "tourism:hostel", "tourism:guest_house", "tourism:apartment", "tourism:motel"]),
    "kultura": (["muzeum", "muzea", "teatr", "kino", "wystaw", "zabyt", "zwiedz", "kości", "bazylik", "kultur"],
                ["tourism:museum", "amenity:theatre", "amenity:cinema", "tourism:attraction", "amenity:place_of_worship",
                 "tourism:gallery", "amenity:arts_centre", "historic:"]),
    "zakupy": (["zakup", "sklep", "galeri", "centrum handlowe"], ["shop:"]),
    "transport": (["dworzec", "pociąg", "autobus", "tramwaj", "przystan"],
                  ["railway:", "public_transport:", "highway:bus_stop", "amenity:bus_station"]),
    "urzędy": (["urząd", "urzęd", "urzad", "załatwi"], ["amenity:townhall", "office:", "amenity:public_building"]),
}


class Candidate(BaseModel):
    place_id: str
    name: str
    kind: str | None
    summary: str
    summary_confidence_pct: int | None
    missing: list[str]
    highlights: list[str]  # najważniejsze informacje o samym miejscu (wejście, schody...)
    sponsorship: Sponsorship | None = None  # tylko aktywna kampania


class SuggestedPlace(BaseModel):
    place_id: str
    name: str
    summary: str
    summary_text: str
    summary_confidence_pct: int | None
    highlights: list[str]
    sponsored: bool = False
    sponsor_name: str | None = None
    tagline: str | None = None
    sponsorship_id: str | None = None


class AssistantAnswer(BaseModel):
    answer: str
    places: list[SuggestedPlace]        # polecane na podstawie danych - bez wpływu reklam
    sponsored: SuggestedPlace | None    # osobne, oznaczone miejsce sponsorowane (albo brak)
    engine: str                         # model AI albo "reguły"
    disclosure: str


DISCLOSURE = (
    "Odpowiedź powstaje wyłącznie z danych aplikacji (OpenStreetMap, zgłoszenia, właściciele). "
    "Miejsca sponsorowane są oznaczone i nie wpływają na oceny ani na odpowiedź asystenta."
)


def question_categories(question: str) -> list[str]:
    q = f"{question.lower()} "
    return [name for name, (words, _) in CATEGORIES.items() if any(w in q for w in words)]


def matches(candidate: Candidate, categories: list[str], question: str) -> bool:
    if candidate.name.lower() in question.lower():
        return True
    if not categories:
        return True
    kind = candidate.kind or ""
    return any(kind.startswith(prefix) for cat in categories for prefix in CATEGORIES[cat][1])


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """Kolejność tylko wg oceny i pewności - reklama nie ma tu wpływu."""
    return sorted(candidates, key=lambda c: (TIER.get(c.summary, 9), -(c.summary_confidence_pct or 0)))


def pick_sponsored(candidates: list[Candidate], categories: list[str], question: str) -> Candidate | None:
    """Co najwyżej jedno miejsce sponsorowane: pasujące do pytania i bez znanych przeszkód dla tego profilu."""
    eligible = [
        c for c in candidates
        if c.sponsorship is not None and TIER.get(c.summary, 9) <= 1 and matches(c, categories, question)
    ]
    return min(eligible, key=lambda c: c.sponsorship.impressions, default=None)  # rotacja: najmniej wyświetlane


def suggested(candidate: Candidate, sponsored: bool = False) -> SuggestedPlace:
    s = candidate.sponsorship if sponsored else None
    return SuggestedPlace(
        place_id=candidate.place_id,
        name=candidate.name,
        summary=candidate.summary,
        summary_text=SUMMARY_PL.get(candidate.summary, candidate.summary),
        summary_confidence_pct=candidate.summary_confidence_pct,
        highlights=candidate.highlights,
        sponsored=sponsored,
        sponsor_name=s.sponsor_name if s else None,
        tagline=s.tagline if s else None,
        sponsorship_id=s.id if s else None,
    )


def rule_answer(question: str, relevant: list[Candidate]) -> tuple[str, list[str]]:
    top = relevant[:3]
    if not top:
        return (
            "Nie mamy jeszcze ocenionych miejsc pasujących do pytania. Wyszukaj konkretne miejsce - "
            "sprawdzimy je dla Twoich ustawień.",
            [],
        )
    lines = []
    for c in top:
        confidence = f", pewność {c.summary_confidence_pct}%" if c.summary_confidence_pct is not None else ""
        lines.append(f"{c.name}: {SUMMARY_PL.get(c.summary, c.summary)}{confidence}.")
    return "Z miejsc, które znamy, dla Twoich ustawień: " + " ".join(lines), [c.place_id for c in top]


SYSTEM_PROMPT = """\
Jesteś asystentem aplikacji „Kraków bez barier”. Pomagasz osobom z różnymi potrzebami (np. poruszanie się bez
stopni, z wózkiem dziecięcym, bez wzroku) wybrać miejsce w Krakowie.

Zasady:
- Korzystaj WYŁĄCZNIE z listy miejsc w danych. Nie wymyślaj miejsc ani informacji.
- Nigdy nie pisz, że miejsce jest „dostępne” - pisz „brak znanych przeszkód”, „niepełne dane” itp., zgodnie z polem ocena.
- Gdy dane są niepełne albo pewność niska, powiedz to wprost.
- Polecaj najwyżej 3 miejsca, najpierw te z najlepszą oceną.
- Jeśli żadne miejsce nie pasuje, powiedz to i zaproponuj wyszukanie konkretnego miejsca.
- Odpowiedź po polsku, najwyżej 4 krótkie zdania, bez list i bez formatowania.
- W place_ids podaj identyfikatory miejsc, które polecasz (z pola id).
"""


class _LlmResult(BaseModel):
    answer: str
    place_ids: list[str]


def llm_answer(
    question: str, relevant: list[Candidate], profile_label: str, client: Any = None, model: str | None = None,
) -> tuple[str, list[str], str]:
    """Odpowiedź małego modelu (Gemini Flash-Lite). Rzuca wyjątek, gdy model niedostępny - wtedy reguły."""
    from google import genai

    client = client or genai.Client()
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    data = [
        {
            "id": c.place_id,
            "nazwa": c.name,
            "rodzaj": c.kind,
            "ocena": SUMMARY_PL.get(c.summary, c.summary),
            "pewnosc_proc": c.summary_confidence_pct,
            "brakuje": c.missing,
            "najwazniejsze": c.highlights,
        }
        for c in relevant[:20]
    ]
    prompt = (
        f"Profil użytkownika: {profile_label}\n"
        f"Pytanie: {question}\n"
        f"Miejsca (oceny dla tego profilu):\n{json.dumps(data, ensure_ascii=False)}"
    )
    interaction = client.interactions.create(
        model=model,
        system_instruction=SYSTEM_PROMPT,
        input=[{"type": "text", "text": prompt}],
        response_format={"type": "text", "mime_type": "application/json", "schema": _LlmResult.model_json_schema()},
        store=False,  # pytania użytkowników nie są przechowywane po stronie dostawcy
    )
    result = _LlmResult.model_validate_json(interaction.output_text)
    known = {c.place_id for c in relevant}
    place_ids = [pid for pid in result.place_ids if pid in known][:3]  # model nie może dodać miejsca spoza danych
    return result.answer.strip(), place_ids, model


def answer_question(
    question: str,
    candidates: list[Candidate],
    profile_label: str,
    use_llm: bool,
    llm_client: Any = None,
) -> AssistantAnswer:
    categories = question_categories(question)
    relevant = rank([c for c in candidates if matches(c, categories, question)])
    engine = "reguły"
    text, place_ids = None, []
    if use_llm:
        try:
            text, place_ids, engine = llm_answer(question, relevant, profile_label, client=llm_client)
        except Exception:  # model niedostępny / limit / błąd odpowiedzi - odpowiadamy regułami
            text = None
            engine = "reguły (model AI niedostępny)"
    if text is None:
        text, place_ids = rule_answer(question, relevant)
    by_id = {c.place_id: c for c in candidates}
    places = [suggested(by_id[pid]) for pid in place_ids]

    sponsor = pick_sponsored(candidates, categories, question)
    if sponsor is not None and sponsor.place_id in place_ids:
        sponsor = None  # i tak polecone na podstawie danych - nie dublujemy go jako reklamy
    return AssistantAnswer(
        answer=text,
        places=places,
        sponsored=suggested(sponsor, sponsored=True) if sponsor else None,
        engine=engine,
        disclosure=DISCLOSURE,
    )

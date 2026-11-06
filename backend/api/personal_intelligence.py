"""Owner-only numerology and astrology-inspired executive guidance.

These values are user-supplied symbolic interpretations, not scientific or financial predictions.
"""
from datetime import date

PROFILE = {
    "name": "Jason Lane Davidson Jr",
    "numerology": {
        "life_path": {"number": 8, "themes": ["leadership", "execution", "financial responsibility"]},
        "birthday": {"number": 7, "themes": ["research", "analysis", "discernment"]},
        "expression_including_jr": {
            "number": 9,
            "themes": ["humanitarian purpose", "service", "broader vision", "legacy"],
        },
        "expression_excluding_jr": {
            "number": 8,
            "themes": ["enterprise", "management", "material stewardship"],
        },
        "calculation_note": (
            "The 9 expression includes the Jr suffix; excluding Jr, the supplied "
            "calculation gives 8. Preserve both conventions rather than silently choosing one."
        ),
    },
    "annual_cycles": {
        "2026": {
            "number": "11/2",
            "themes": ["preparation", "intuition", "partnerships", "cooperation"],
            "business_focus": [
                "validate the data supply chain and licensing",
                "build trusted strategic partnerships",
                "strengthen security, governance, and tenant isolation",
            ],
        },
        "2027": {
            "number": 3,
            "themes": ["expression", "creativity", "communication", "visibility"],
            "business_focus": [
                "publish credible case studies and product demonstrations",
                "expand customer education and market visibility",
                "turn validated capabilities into a clear sales narrative",
            ],
        },
    },
    "astrology": {
        "birth_details": {"date": "1996-12-25", "local_time": "09:30", "birthplace": "Indianapolis, Indiana, USA"},
        "birth_chart_status": "placements_not_yet_independently_verified",
        "interpretation": (
            "Do not infer planetary placements or aspects until a chart is calculated "
            "from the confirmed birth date, time, and location."
        ),
    },
    "disclaimer": (
        "Numerology and astrology are optional symbolic reflection tools. They do not "
        "predict revenue, investment returns, customer behavior, or business outcomes. "
        "Operational decisions must be based on evidence, customer research, and financial controls."
    ),
}


def executive_guidance(year: int | None = None) -> dict:
    selected_year = year or date.today().year
    cycle = PROFILE["annual_cycles"].get(str(selected_year))
    return {
        "profile": PROFILE,
        "selected_year": selected_year,
        "current_cycle": cycle,
        "guidance": (
            cycle["business_focus"] if cycle else
            ["Use measured business evidence and quarterly operating goals; no annual symbolic cycle is configured."]
        ),
    }

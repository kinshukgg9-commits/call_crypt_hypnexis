"""User-facing copy, served from /api/config so the UI and the backend never disagree.

The verdict titles and messages are also embedded in every verdict object, so a
frontend can simply print them.
"""
from typing import Any

MESSAGES: dict[str, Any] = {
    # The call-level result: three main outcomes, plus "inconclusive".
    "verdict": {
        "safe": {
            "title": "Not a scam call",
            "message": "Nothing said on this call matches known scam tactics.",
        },
        "ai_voice": {
            "title": "AI-generated voice, likely not a scam",
            "message": (
                "The voice shows signs of being computer-generated, but what was said does not add up "
                "to a scam. Be careful if the caller asks for money, codes or personal details."
            ),
        },
        "scam": {
            "title": "Scam call detected",
            "message": (
                "This call matches known scam tactics. Don't share OTPs or transfer money. Hang up "
                "and verify through a number you trust. India: helpline 1930 / cybercrime.gov.in."
            ),
        },
        "inconclusive": {
            "title": "Not enough to judge",
            "message": "There wasn't enough clear speech to analyse.",
        },
    },
    # Headlines while the call is still going: nothing is final until it ends.
    "verdict_live_titles": {
        "safe": "No scam detected so far",
        "ai_voice": "AI-generated voice, no scam detected so far",
        "inconclusive": "Listening…",
    },
    "verdict_notes": {
        "weak_cues": "A few phrases resembled scam tactics, but not enough to call this a scam.",
        "voice_natural": "No signs of an AI-generated voice were found.",
        "voice_some": "A brief part of the voice sounded synthetic, which is too little to call it AI-generated.",
        "voice_ai": "The voice also shows signs of being AI-generated.",
        "voice_unchecked": "Voice authenticity could not be checked.",
        "transcript_unavailable": "Speech recognition is unavailable, so what was said could not be checked.",
    },
    # Per-window copy
    "risk": {
        "low": "No scam cues detected in this window.",
        "medium": (
            "This conversation includes a possible scam cue (reasons below). Don't share codes or "
            "money. Verify the caller through a contact method you already trust."
        ),
        "high": (
            "Several scam cues detected. Don't share OTPs or transfer money. Hang up and verify "
            "independently. India: helpline 1930 / cybercrime.gov.in."
        ),
    },
    "voice": {
        "normal": "No synthetic-speech patterns flagged in this window.",
        "elevated": "Audio shows patterns sometimes seen in synthetic speech. This is not proof.",
        "insufficient_audio": "Not enough clear speech to assess voice authenticity.",
        "clipped": "Audio is too distorted to assess voice authenticity.",
        "unavailable": "Voice authenticity check is unavailable right now.",
    },
    "transcript": {
        "no_speech": "No speech detected in this window.",
        "unavailable": "Speech recognition is unavailable, so scam cues can't be checked right now.",
    },
    "disclaimer": "Results are automated risk indicators, not proof. When in doubt, hang up and verify.",
    "privacy": (
        "Audio is streamed to the VoiceGuard server for analysis, held in memory only while "
        "this session is open, and never stored."
    ),
}

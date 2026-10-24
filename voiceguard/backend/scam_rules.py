"""Rule-based scam-cue scoring over what was said on the call.

Each category is one kind of scam tactic with a weight; categories combine with a
noisy-OR. Weights are tiered so that a single everyday phrase ("right now", "pay the
amount", "the police") never reaches the scam verdict on its own, while requests for
secrets and scam-only phrases ("digital arrest", "safe account") do.

Covers English, romanized Hindi (Hinglish) and Devanagari, because Whisper may
output either script for Hindi speech. Tune the weights on a labeled set
(scripts/eval_rules.py) and have a native speaker review the Hindi patterns.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

SENTENCE_END = "[.?!;।]"


def gap(n: int) -> str:
    """Up to n chars, but never across "and" / "but" / a sentence end."""
    return r"(?:(?!\b(?:and|but)\b|" + SENTENCE_END + r").){0," + str(n) + "}"


def hgap(n: int) -> str:
    """Like gap(), for romanized Hindi: also never across a negation ("otp mat batao")."""
    return r"(?:(?!\b(?:mat|na|nahi|nahin)\b|" + SENTENCE_END + r").){0," + str(n) + "}"


def dgap(n: int) -> str:
    """Like hgap(), for Devanagari, where \\b is unreliable (vowel signs are not word chars)."""
    return r"(?:(?!मत|नहीं|\sन\s|\sना\s|" + SENTENCE_END + r").){0," + str(n) + "}"


# "send me the wifi password" is not a request for a financial secret
_EVERYDAY_PASSWORD = "".join(
    f"(?<!{w} )" for w in ("wifi", "wi-fi", "netflix", "hotspot", "meeting", "zoom", "laptop")
)
_SECRET = (
    r"\b(otp|one[- ]time password|pin|mpin|cvv|" + _EVERYDAY_PASSWORD + r"password|passcode|aadhaar|aadhar"
    r"|pan (card )?number|card number|account number|expiry date|verification code|security code)\b"
)

# name: (weight, patterns), strongest first.
CATS: dict[str, tuple[float, list[str]]] = {
    "Known scam script": (0.65, [            # phrases that are close to unique to scams
        r"digital arrest", r"\b(safe|verification) account\b", r"\bmoney laundering\b",
        r"\b(parcel|courier|package|consignment)\b" + gap(50) + r"\b(drugs|narcotics|illegal|contraband)\b",
        r"डिजिटल ?अरेस्ट", r"(सेफ|वेरिफिकेशन) ?अकाउंट", r"मनी ?लॉन्ड्रिंग",
    ]),
    "Sensitive info request": (0.60, [
        r"\b(tell|give|share|send|read|say|provide|enter|confirm|type)\b" + gap(25) + _SECRET,
        r"\b(otp|pin|cvv)\b" + hgap(12) + r"\b(batao|bataiye|bhejo|bata do|de do)\b",
        r"(ओटीपी|पिन(?!कोड)|सीवीवी|पासवर्ड|आधार)" + dgap(15)
        + r"(बताओ|बताइए|बताइये|बताएं|बता दो|बता दीजिए|भेजो|भेजिए|भेज दो|दे दो|दीजिए)",
    ]),
    "Threat or confinement": (0.55, [        # aimed at the listener, not a mention of an arrest in the news
        r"\byou (will|would|could|can|may|shall|are going to) be arrested\b",
        r"\b(you are|you're) under arrest\b", r"\barrest you\b",
        r"\bwarrant\b" + gap(20) + r"\b(your name|against you|for you)\b",
        r"\b(case|fir|complaint)\b" + gap(30) + r"\b(against you|(in|on) your name)\b",
        r"\blegal action\b" + gap(20) + r"\bagainst you\b",
        r"(do not|don'?t|cannot|can'?t) (leave|disconnect|hang up|cut the call)",
        r"stay on (the )?(video|call)",
        r"\b(giraftar|giraftaar|griftar)\b",
        r"\b(phone|call) (mat|na) (kaatna|katna|kaato|kato|kaatiye|katiye)\b",
        r"गिरफ़?्तार", r"वारंट",
        r"(फोन|फ़ोन|कॉल) (मत|ना|न) (काटना|काटें|काटिए|काटो)",
    ]),
    "Remote access request": (0.50, [
        r"\b(any ?desk|team ?viewer|quick ?support|rust ?desk)\b",
        r"\b(install|download|open)\b" + gap(25) + r"\b(apk|screen ?sharing app|remote access app|support app)\b",
    ]),
    "Money movement": (0.45, [
        r"\b(transfer|send|deposit|pay)\b" + gap(40)
        + r"\b(money|amount|funds|rupees|rs\.?|lakh|lakhs|crore|fee|fees|fine|penalty|charges)\b",
        r"\b(paise|paisa|rakam|rupaye|rupay|lakh)\b" + hgap(30) + r"\b(transfer|bhejo|bhejiye|bhej do|jama)\b",
        r"(पैसे|पैसा|रकम|रुपये|रुपए|लाख|फंड)" + dgap(30) + r"(ट्रांसफर|भेजो|भेजिए|भेज दो|जमा)",
    ]),
    "Secrecy": (0.45, [
        r"(do not|don'?t|never) tell (anyone|anybody|your family)",
        r"\bkeep (this|it) (secret|confidential)\b",
        r"kisi ko (bhi )?(mat|na) (batana|batao|bataye|bataiye|bataen)",
        r"किसी (को|से) (भी )?(मत|ना|न) (बताना|बताएं|बताइए|बताओ|बताइये)",
        r"गुप्त (रखें|रखना|रखिए)",
    ]),
    "Prize or refund bait": (0.40, [
        r"\b(won|win|winner|selected)\b" + gap(40) + r"\b(lottery|prize|lucky draw|reward|jackpot|gift voucher)\b",
        r"\b(lottery|lucky draw|jackpot)\b",
        r"\b(processing|registration|clearance) (fee|fees|charge|charges)\b",
        r"\bclaim\b" + gap(25) + r"\b(refund|cashback|prize|reward)\b",
        r"लॉटरी", r"\blottery (lagi|lag gayi|nikli)\b",
    ]),
    "Account or KYC threat": (0.35, [
        r"\b(account|card|sim|sim card|kyc|number|connection|upi|wallet)\b" + gap(30)
        + r"\b(blocked|block|suspended|suspend|deactivated|deactivate|disconnected|frozen|freeze|expired|expire)\b",
        r"\bkyc\b" + gap(20) + r"\b(update|pending|incomplete|verification)\b",
        r"\b(update|complete)\b" + gap(15) + r"\bkyc\b",
        r"\b(account|khata|sim|card)\b" + hgap(15) + r"\b(band|block) (ho|kar)",
        r"(खाता|अकाउंट|सिम|कार्ड)" + dgap(20) + r"(बंद|ब्लॉक)",
    ]),
    "Authority impersonation": (0.25, [
        r"\b(cbi|police|cyber ?crime|customs|rbi|income tax|narcotics|court)\b",
        r"(सीबीआई|पुलिस|साइबर ?क्राइम|कस्टम्स|आरबीआई|इनकम ?टैक्स|आयकर|नारकोटिक्स|कोर्ट|अदालत)",
    ]),
    "Urgency": (0.20, [
        r"\bimmediately\b", r"\bright now\b", r"\b(turant|fauran)\b", r"तुरंत", r"फ़?ौरन",
    ]),
}
CATEGORY_WEIGHTS: dict[str, float] = {name: w for name, (w, _) in CATS.items()}
NEGATABLE = {"Sensitive info request", "Money movement"}      # "I will never share an OTP"
NEG = re.compile(r"\b(never|not|no|don'?t|won'?t|nahi|nahin)\b[^.?!।]{0,12}$")

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})


def _normalize(text: str) -> str:
    # NFC also splits precomposed nukta letters (e.g. U+095E) into letter + nukta,
    # so text and patterns (normalized the same way below) always agree.
    return unicodedata.normalize("NFC", text).lower().translate(_APOSTROPHES)


_COMPILED = [
    (name, w, [re.compile(unicodedata.normalize("NFC", p)) for p in pats]) for name, (w, pats) in CATS.items()
]


def _negated(name: str, text: str, start: int) -> bool:
    return name in NEGATABLE and NEG.search(text[max(0, start - 30):start]) is not None


def combine(categories: Iterable[str]) -> float:
    """Noisy-OR of the category weights: 1 - prod(1 - w). Unknown names are ignored."""
    miss = 1.0
    for name in set(categories):
        miss *= 1 - CATEGORY_WEIGHTS.get(name, 0.0)
    return round(1 - miss, 2)


def order(categories: Iterable[str]) -> list[str]:
    """Categories in the canonical (strongest-first) order."""
    seen = set(categories)
    return [name for name in CATS if name in seen]


def score_text(text: str) -> tuple[float, list[str]]:
    """Return (score in [0, 1], matched category names, strongest first)."""
    t, hits = _normalize(text), []
    for name, _w, pats in _COMPILED:
        for p in pats:
            if any(not _negated(name, t, m.start()) for m in p.finditer(t)):
                hits.append(name)
                break
    return combine(hits), hits

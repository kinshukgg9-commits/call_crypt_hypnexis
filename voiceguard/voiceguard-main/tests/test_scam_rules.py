from pathlib import Path

import pytest

from backend.scam_rules import CATEGORY_WEIGHTS, combine, order, score_text

LABELED = Path(__file__).parent / "data" / "scam_lines.tsv"
SCAM_VERDICT = 0.60


# -- the three tests from the build plan ------------------------------------------
def test_scam_script():
    s, r = score_text("Do not tell anyone and read me the OTP")
    assert s >= 0.7 and "Sensitive info request" in r


def test_negation_is_safe():
    assert score_text("I will never share an OTP") == (0.0, [])


def test_benign_mention():
    assert score_text("Your OTP for the order is 4821, do not share it with anyone")[0] == 0.0


# -- scoring ----------------------------------------------------------------------
def test_empty_text():
    assert score_text("") == (0.0, [])


def test_noisy_or_combination_and_order():
    s, r = score_text("CBI here. You are under digital arrest. Transfer the money to the safe account immediately.")
    assert r == ["Known scam script", "Money movement", "Authority impersonation", "Urgency"]
    assert s == round(1 - 0.35 * 0.55 * 0.75 * 0.8, 2) == combine(r)
    assert order(reversed(r)) == r
    assert combine([]) == 0.0 and combine(["Urgency", "Urgency", "not a category"]) == 0.2


def test_only_strong_tactics_convict_alone():
    alone = {name for name, w in CATEGORY_WEIGHTS.items() if w >= SCAM_VERDICT}
    assert alone == {"Known scam script", "Sensitive info request"}


def test_curly_apostrophe_negation():
    assert score_text("I won’t give my password to anyone") == (0.0, [])


def test_gap_does_not_cross_sentence_end():
    assert score_text("Send me the report. The OTP expired.") == (0.0, [])


# -- threats must be aimed at the listener ---------------------------------------------
@pytest.mark.parametrize("text", [
    "You will be arrested if you do not cooperate",
    "There is an arrest warrant in your name",
    "A case has been registered against you",
    "Do not hang up",
    "We will take legal action against you",
])
def test_personal_threats(text):
    assert "Threat or confinement" in score_text(text)[1]


@pytest.mark.parametrize("text", [
    "Did you hear the police arrested that actor yesterday?",
    "The court issued a warrant for the minister",
    "He was arrested last week",
])
def test_news_about_arrests_is_not_a_threat(text):
    assert "Threat or confinement" not in score_text(text)[1]
    assert score_text(text)[0] < SCAM_VERDICT


# -- newer scam types -------------------------------------------------------------------
@pytest.mark.parametrize("text, reason", [
    ("Please install AnyDesk so I can fix it", "Remote access request"),
    ("Download this APK and open it", "Remote access request"),
    ("Your account will be blocked today", "Account or KYC threat"),
    ("Your KYC is pending", "Account or KYC threat"),
    ("Your SIM card will be deactivated in two hours", "Account or KYC threat"),
    ("You have won a lottery of 25 lakh", "Prize or refund bait"),
    ("Pay the processing fee to claim your prize", "Prize or refund bait"),
    ("Your parcel contains illegal drugs", "Known scam script"),
    ("Your Aadhaar is linked to a money laundering case", "Known scam script"),
    ("Tell me the one time password", "Sensitive info request"),
    ("Confirm your card number and expiry date", "Sensitive info request"),
    ("Enter your UPI PIN to receive the money", "Sensitive info request"),
])
def test_scam_tactics(text, reason):
    assert reason in score_text(text)[1]


@pytest.mark.parametrize("text", [
    "Can you send me the wifi password?",
    "Tell me the Netflix password again",
    "Share your screen so I can see the slide",
    "Send me five hundred on GPay right now, I'll return it tomorrow",
    "I need to pay the income tax amount by Friday",
    "Please stay on the call while I check your order",
    "My card got blocked so I went to the branch",
])
def test_everyday_talk_stays_below_the_scam_verdict(text):
    assert score_text(text)[0] < SCAM_VERDICT


# -- Hindi and Hinglish -------------------------------------------------------------------
@pytest.mark.parametrize("text, reason", [
    ("jaldi se otp batao", "Sensitive info request"),
    ("kisi ko mat batana", "Secrecy"),
    ("aap giraftar ho jaoge", "Threat or confinement"),
    ("paise transfer karo", "Money movement"),
    ("aapka account band ho jayega", "Account or KYC threat"),
    ("turant", "Urgency"),
    ("अपना ओटीपी बताइए", "Sensitive info request"),
    ("आप डिजिटल अरेस्ट में हैं", "Known scam script"),
    ("मैं सीबीआई से बोल रहा हूँ", "Authority impersonation"),
    ("पैसे तुरंत ट्रांसफर कीजिए", "Money movement"),
    ("फोन मत काटना", "Threat or confinement"),
    ("आपका खाता बंद हो जाएगा", "Account or KYC threat"),
])
def test_hindi_and_hinglish_cues(text, reason):
    assert reason in score_text(text)[1]


@pytest.mark.parametrize("text", [
    "otp mat batao kisi ko",          # "don't tell the OTP": a warning, not a request
    "ओटीपी मत बताओ",
    "ओटीपी किसी के साथ शेयर न करें",
    "अपना पिनकोड बताइए",              # pincode, not PIN
    "customer care se baat karo",
])
def test_hindi_benign(text):
    assert score_text(text) == (0.0, [])


def test_precomposed_nukta_is_normalized():
    precomposed = "गिरफ़्तार"   # गिरफ़्तार with U+095E
    assert "Threat or confinement" in score_text(precomposed)[1]


# -- labeled set ----------------------------------------------------------------------------
def _labeled():
    rows = []
    for line in LABELED.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            label, text = line.split("\t", 1)
            rows.append((int(label), text))
    return rows


def test_labeled_set_precision_and_recall():
    rows = _labeled()
    assert len(rows) >= 60
    tp = fp = fn = 0
    for label, text in rows:
        flagged = score_text(text)[0] >= SCAM_VERDICT       # would reach the scam verdict
        tp += flagged and label == 1
        fp += flagged and label == 0
        fn += (not flagged) and label == 1
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    assert recall >= 0.90, f"recall {recall:.2f}"
    assert precision >= 0.90, f"precision {precision:.2f}"

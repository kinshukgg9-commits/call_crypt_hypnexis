# Datasheet: VGSC-25 (VoiceGuard Scam Calls 2025), version 1.0

Follows the structure of *Datasheets for Datasets* (Gebru et al., 2021). Prepared 2026-10-24.

## 1. Motivation

**Purpose.** A held-out test set for the VoiceGuard backend: does the full pipeline (speech-to-text,
scam rules, verdict, synthetic-voice check) flag the scam calls that were actually reported in
2025, and does it stay quiet on genuine calls that sound alike? It is for **testing only**. Do not
train or tune on it, or its numbers stop meaning anything.

**Who made it.** Generated for the VoiceGuard project. No external funding.

## 2. Composition

| Part | Files | Count | What one item is |
|---|---|---|---|
| Incident registry | `incidents.jsonl`, `sources.csv` | 25 records, 40 sources | One real 2025 scam incident, campaign, statistic, enforcement action or policy, with a verification level |
| Excluded candidates | `excluded.json` | 10 | A case we looked at and rejected, with the reason |
| Call scripts | `calls.jsonl` | 57 calls (31 scam, 26 genuine) | A two-party call transcript; every scam call points to its incident |
| Call audio | `audio/*.flac`, `audio/manifest.json` | 171 clips, 54.6 min | One call spoken by stock TTS voices, in one phone condition |
| Partial fakes | `partial_fakes/clips/*.wav`, `manifest.json` | 130 clips, 16.0 min | The earlier partial-fakes-v2 set: real reader with a synthetic scam line spliced in (100), or genuine (30) |
| Baseline results | `results/text.json`, `results/voice.json` | | Today's backend on this set |

**Scam calls by type:** digital arrest 17, official impersonation 6, official impersonation with AI
voice 4, family emergency with cloned voice 3, family emergency 1.
**Genuine calls (hard negatives):** real bank fraud alerts, police and lawyer calls, people talking
*about* digital arrest scams, family members asking for money, delivery, utility and telecom
reminders, and others (18 kinds).
**Languages:** English 41, Hinglish (Hindi in Latin script) 11, Hindi in Devanagari 5.
**Countries of the incidents:** India 9, US 7, Singapore 6, Canada 2, Italy 1.

**Labels.**
- Call label: `1` scam, `0` genuine. A scam call's label comes from its incident; a genuine call is
  written to be legitimate.
- Turn basis: `reported` (the line paraphrases what a source says the scammer said; 111 turns) or
  `filler` (written to make the call flow, not sourced; 63 turns).
- Voice label of every rendered clip: `synthetic`. The voices are TTS, so these clips test the scam
  check on content and the voice check on unseen generators. They are not real-voice calls.
- Partial fakes: per-segment and per-0.5 s-frame real/fake labels, as in the earlier report.

**Incident fields.** id, record_type, date, date_precision, country, region, scam_type,
claimed_identity, channel, ai_voice, victim_profile, loss, currency, loss_usd_approx, recovered,
outcome, tactics, summary, note, sources, verification_level.
`ai_voice` is one of `confirmed_by_authority`, `reported_by_media`, `alleged_by_victim`,
`not_reported`, `not_applicable`. Most 2025 cases are `not_reported`: an AI voice is claimed far
more often than it is shown.

**Missing or withheld.** No victim is named. No real call audio is included (see section 4).
Losses are as reported, not audited; `loss_usd_approx` uses fixed rates (INR 86, EUR 1/1.13,
SGD 1/0.76, CAD left blank) and is only for sorting.

## 3. Collection and verification

**Window.** Calendar 2025, by the date of the call, the complaint, or the advisory. Cases reported
in 2025 about earlier calls, and 2026 cases, are excluded (`excluded.json`).

**Search.** Official advisories (FBI IC3, FTC, Singapore Police Force, India's Supreme Court, TRAI)
and news reports in English, read on 2026-10-24. Every source row says which fields it supports,
whether we read it (`fetched`), and when.

**Verification levels.**

| Level | Rule | Records |
|---|---|---|
| A | A primary official source was read and supports the record | 8 |
| B | Two or more independent sources were read and agree (wire copies count once) | 8 |
| C | One source was read | 9 |

A source we could not open (blocked site) is listed with `fetched=False` and does not count
towards the level.

**From incident to call script.** Each scam call paraphrases the scammer lines a source reports,
in order, marked `reported`; the victim's lines and joins are `filler`. Nothing is quoted at length,
and no line is presented as a verbatim recording.

**Audio.** `scripts/render_scam_calls.py`: caller and callee in different offline stock voices
(eSpeak NG, MBROLA, Festival, Flite, Pico), equal loudness, short gaps, then three conditions:
`clean`, `g711_mulaw` (8 kHz phone codec) and `phone_worst` (GSM codec plus noise at 10 dB SNR).
16 kHz, 16-bit FLAC. Every file's SHA-256 is in the manifest.

## 4. Held-out checks

- **Text:** `scripts/check_benchmark_overlap.py` compares every line with the lines the scam rules
  were tuned on (`tests/data/scam_lines.tsv`): same after normalising, or half their word trigrams
  shared. **No overlap.**
- **Voices:** none of the TTS engines is among the voice model's training generators
  (`ml/train/train.py`).
- **Audio:** run the same script with `--train-cache data/voice/cache` after `train prep` to hash
  every clip against the training audio. **Not run here** (no training cache in this copy).
  **Known risk:** the partial fakes use LibriSpeech readers, and the training set includes audiobook
  speech. Run the check before trusting the partial-fake numbers for a newly trained model.

## 5. Uses

**Intended:** testing a trained backend; comparing versions; finding which scam types the rules miss.
**Not intended:** training or tuning; estimating how common scams are; anything about the people in
the incidents.

## 6. Limits

- Small: 31 scam calls. A change of one call moves recall by 3 points.
- The calls are reconstructions, read by robot-sounding voices. Real calls are messier, faster and
  noisier. Text results assume perfect speech-to-text.
- English-language sources only; Indian regional languages other than Hindi are absent.
- No verified 2025 Indian case with a proven AI voice was found. The Indian AI-voice cases we found
  are all from 2026 and are excluded.
- Real-voice recordings are not in this version (see `RECORDING.md`).

## 7. Ethics and privacy

No victim names, phone numbers or account details. Public officials named in the incidents are
named as the *impersonated* party only, as the sources do. All voices are stock TTS; none imitates a
real person. Suicide is mentioned in one incident (IN-2025-001) as reported.

## 8. Distribution and licence

Scripts, metadata and audio: CC BY 4.0. Source URLs point to third-party articles under their own
terms; we store only short paraphrases and bibliographic data. Code: the VoiceGuard repository's
licence.

## 9. Maintenance

Regenerate with `python benchmark/vgsc25/incidents_src.py`, `python benchmark/vgsc25/calls_src.py`,
then `python scripts/render_scam_calls.py`. Bump the version on any change to calls or audio.

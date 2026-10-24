# Recording real-voice versions of the VGSC-25 calls

Version 1.0 ships only synthetic voices. The voice check needs real human voices too, so it can be
measured on false alarms (a real caller wrongly called AI) and so the scam check is tested on
real speech-to-text. This is how to record them safely. Do not use recordings of real scam calls,
and do not ask anyone to imitate a real person.

## People and consent

- Adults only (18+), who volunteer. At least 10 people, mixed gender and age, and for the Hinglish
  and Hindi calls, people who speak them every day.
- Each person signs a consent form before recording. It says: what the recordings are for (testing
  a scam detector), that they will be kept in the test set and may be shared under CC BY 4.0
  (or kept private if they choose "internal only"), that they can withdraw at any time and their
  files will be deleted, and that their name is never stored with the audio.
- Give each person a code (`spk-01`, `spk-02`, ...). Keep the code-to-name list apart from the data,
  with access for one person only.
- Tell them some scripts are about threats and arrest; anyone can skip a script.

## What to record

- Each person reads one role (caller or callee) of 6 to 10 calls from `calls.jsonl`, with a partner
  reading the other role, over a real phone call. Balance scam and genuine calls (the genuine ones
  matter as much: they measure false alarms).
- Read naturally, as a call. Mistakes and restarts are fine; keep them in.
- Record both ends of the call if you can, or just the far end as the app would hear it.

## How to record

- Use the VoiceGuard app's own recording path or a phone call recorder; note the phone model,
  network (mobile, VoIP, landline) and place (quiet room, street, car).
- Save as WAV or FLAC, mono, at least 16 kHz. Don't edit, denoise or normalise.
- Name files `<call_id>_<speaker code>_<phone|voip|landline>.flac`, e.g. `call-008_spk-03_phone.flac`.

## Labelling

For each file, add a line to `audio_real/manifest.json` with the same fields as
`audio/manifest.json`, plus `voice_label: "real"`, `speakers: ["spk-03", ...]`, `device`,
`network` and `environment`. Fill turn start and end times with any aligner, or leave them empty.
Run `python scripts/check_benchmark_overlap.py --train-cache data/voice/cache` before using it,
and never add these speakers to training data.

## Before sharing

Listen to every file once. Delete any where a real name, number or address was said by accident,
or bleep it. Check that every speaker's consent covers the sharing you plan.

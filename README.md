# CallCrypt (VoiceGuard)

Listens to a phone call, live or recorded, and flags scam tactics and AI-generated voices.

**Live demo:** https://callcrypthypnexis-frontend-89vl.vercel.app

| Part | Where it runs | Link |
|---|---|---|
| Frontend | Vercel | https://callcrypthypnexis-frontend-89vl.vercel.app |
| Backend API | Render | https://voiceguard-api-3x3m.onrender.com ([health](https://voiceguard-api-3x3m.onrender.com/health)) |

The backend is on Render's free plan, so the first request after it has been idle can take about a minute.

The app is in [`voiceguard/`](voiceguard/). See [`voiceguard/README.md`](voiceguard/README.md) for how it works and how to run it.

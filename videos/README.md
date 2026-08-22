# Tutorial videos

Step-by-step tutorials for the field operation, with natural Spanish narration
(es-CO neural voice) and on-screen subtitles. A matching `.srt` file ships next
to each video for players with toggleable captions.

| Video | Audience | Content |
| --- | --- | --- |
| `tutorial-formulario-atc20.mp4` | Field inspectors | Filling the ATC-20 form at <https://formulario-atc20-cali.vercel.app/>: login with cedula + default password `Cali2026+-`, GPS location, building code, classification, photos and submit. |
| `tutorial-stickers-dashboard.mp4` | Dashboard admins | The "Stickers" tab: evaluation KPIs and map, record detail with photos, creating inspectors (brigade code is auto-assigned), enabling/disabling accounts. |

## Regenerating

The videos are produced by the scripts in `tools/` (Playwright recording +
edge-tts narration + ffmpeg mux). The dashboard tutorial runs the real web
modules against a stateful in-page API stub — nothing touches production. The
form tutorial records the production form with a real inspector login but
never presses "Enviar evaluación".

```
node videos/tools/video-formulario.mjs <cedula> <password> <sample-photo.jpg> videos
node videos/tools/video-stickers.mjs videos
```

Requirements: `formulario/node_modules` (Playwright), `edge-tts` in the repo's
`.venv`, and ffmpeg (winget Gyan.FFmpeg).

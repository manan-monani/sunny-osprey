# Gazeguard on a Windows laptop: quick start

Everything (AI model, webcam analysis, dashboard, private alerts) runs in Docker on this laptop.
Tested on: Windows 11, 16 GB RAM, NVIDIA RTX 2050 (4 GB), Docker Desktop.

## Start (one click)

Double-click **`start.bat`** in the project folder.

It will:
1. start Docker Desktop if it is not running,
2. build the AI model on the very first run (~11 GB download + ~10 min, once),
3. start all services and wait until they are ready,
4. open three browser tabs: **camera**, **dashboard** and **alerts**.

Then, in the browser:
1. **Camera tab**: allow the camera, click **Publish**, keep the tab open.
   (Lost it? Use **📷 Connect camera** at the top of the dashboard.)
2. **Alerts tab**: click **Grant now** once to get desktop notifications.
3. **Dashboard** (http://localhost:8080): live preview and every analyzed event.

Something moving in front of the camera is analyzed about 8 s later. Suspicious events (theft, intrusion,
fall/medical emergency, tampering) raise an alert in the alerts tab.

## Stop

Double-click **`stop.bat`**. Models, settings and alert history are kept.

## Addresses

| What | Address |
|---|---|
| Dashboard | http://localhost:8080 |
| Camera publish page | **📷 Connect camera** button on the dashboard |
| Private alerts | **🔔 Alerts** button on the dashboard (topic set in `gazeguard-config.yaml`) |
| Model server (OpenAI API + chat UI) | http://localhost:8081 |

Everything listens on this laptop only (127.0.0.1), so nobody else on your Wi-Fi can see the camera.

## Camera must be H.264

Browsers publish AV1 by default, which the analysis cannot decode. The camera link from `start.bat` and the
dashboard button set H.264 for you. If you open http://localhost:8889/webcam/publish by hand, choose
**H264** as the video codec. The webcam log says so when the codec is wrong:

```
docker compose -f docker-compose.webcam.yaml logs -f webcam
```

## Useful commands

```
docker compose -f docker-compose.webcam.yaml ps                                   # service status
docker compose -f docker-compose.webcam.yaml logs -f webcam                       # live analysis log
docker compose -f docker-compose.webcam.yaml --profile test run --rm clip-tests   # accuracy check (7 clips)
```

## Settings worth knowing (`gazeguard-config.yaml`)

- `motion.cooldown_seconds`: minimum time between analyses (default 12 s)
- `motion.min_area`: motion sensitivity (lower = more sensitive)
- `alerts.send_all_activities`: `true` also sends "normal activity" notifications
- `alerts.backend`: `ntfy` (private alerts), `none` (dashboard only)
- `.env` (not committed): `TZ=Asia/Kolkata` for local times; `ALERT_BACKEND=none` to mute alerts

## Model

Default: **Gemma 3n E2B** on llama.cpp (7/7 on the test clips, ~8 s per event, fits the 4 GB GPU).
Gemma 4 E2B is available as a faster but less accurate alternative (4/7); see the `LLM_*` variables in
`docker-compose.webcam.yaml` and `scripts/prepare_gemma4.sh`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `start.bat` says Docker did not start | Open Docker Desktop by hand, wait for "Engine running", run `start.bat` again |
| No events on the dashboard | Camera tab must stay open and publishing; check the webcam log above |
| Webcam log: "cannot be decoded" | Re-publish with the **📷 Connect camera** button (H.264) |
| No desktop notifications | Alerts tab must be open and notifications allowed for localhost:8090 |
| Laptop low on memory | Docker is capped at 6 GB via `%UserProfile%\.wslconfig`; run `stop.bat` when not monitoring |

# FPV Flight Desk — Admin + Officer

This is a local-first operations app for the attached `Viskas.xlsx` workflow. It is designed to run on the admin laptop and be opened from phones, the officer laptop, and a second telemetry laptop over the same hotspot.

## Workbook facts used

The workbook contains the `SKRYDzIAI` ledger with 1,920 flight rows (`FL-000001` through `FL-001920`), a `Sesija` column, and 20 session slots shown in `Valandos` as `Sesija 1` through `Sesija 20`. The ledger currently contains placeholder values such as `PILOT-0`, `SC-`, and `REP-0` in much of the template, so the app does **not** silently trust those broken placeholders. It generates the intended 20 pilots × 6 scenarios × 2 modes × 2 weather conditions × 4 repetitions matrix and exposes the session ID explicitly for every flight.

## Three interfaces

Open `/admin` on the admin laptop. This is the control console with the large pilot queue, previous/current/future flight views, session selector, pilot tags, two blackbox bays, exact telemetry assignment dropdowns, and the video queue.

Open `/officer` on the officer device. This is the operational board with previous/current/future flights, flight queue, video queue, pilot statuses, and the same session context. The telemetry assignment controls are hidden so the officer cannot accidentally reassign files.

The interface is responsive for phones and laptops.

Open `/collector` on the second laptop. This is the two-bay collection station: it can select exact flights and assign RAW/video files, but it has no Claim, Fly, or Land controls. The admin laptop remains the only device that controls who flies next. Use Blackbox bay 1 for collector pilot A and Blackbox bay 2 for collector pilot B; select the exact flight in each bay before assigning a file.

## Run

```bash
cd fpv_ops
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5050/admin`. On other devices connected to the laptop hotspot, open `http://HOTSPOT_IP:5050/admin` or `/officer`.

## Field workflow

The admin selects or claims the next pilot from the queue. If a pilot has an SD-card workflow, click **Tag SD-card**. This tag remains attached to the pilot's flights and makes the exception visible. SD-card pilots can fly repeatedly without waiting for the blackbox queue; the officer still sees each flight state.

When a drone lands, the admin uses **Land**. Every fourth repetition creates a video queue item for that pilot. The pilot can then bring the goggles/video to the admin without being asked for the same information repeatedly.

For telemetry, use either blackbox bay. The admin selects the exact flight from the dropdown, chooses the source device, selects the `.bbl` file, and clicks **Assign RAW**. The file is copied into a temporary `.partial` file and moved into its canonical final name only after the copy succeeds. This avoids attaching telemetry to the wrong flight and avoids incomplete final files.

The separate video queue works the same way. Select the exact queued flight, choose the MP4, and assign it. The app marks the flight `COMPLETE` only when both RAW and video exist.

## Second telemetry laptop

Run the same app on the second laptop with a different local data directory and open its `/admin` interface. Use `device=LAPTOP-2` in the built-in second bay, or use the admin laptop as the single source of truth if the second laptop is only used for file gathering. Each machine saves files locally. At the end of the session, copy the `data/blackbox` and `data/video` folders manually as requested.

The application also records sync events and includes `/api/sync/events` and `/api/sync/push` endpoints so a later one-command peer synchronizer can replay flight metadata over the hotspot. The current operational mode intentionally avoids automatic destructive copying: it never deletes source media and never overwrites a canonical filename.

## File names

Example RAW name:

```text
FL-000001_PILOT-001_SC-01_CALM_W1_REP-01.bbl
```

The matching MP4 uses the same stem. This makes manual end-of-session copying and later Google Sheet reconciliation straightforward.

## Google Sheets

This build is local-first and does not require internet or Google credentials during a flight session. Use `Export CSV` after the session and import into the spreadsheet. This is safer during field work than making every transfer depend on internet availability. The `Viskas.xlsx` workbook remains the reference for the session IDs and registry structure.

## Safety and limits

The app can identify and assign files, coordinate people, and maintain a synchronized operational state. It cannot physically operate every Betaflight or goggles device without a vendor-specific integration. The operator must still produce the `.bbl` and `.mp4` files. Validate the setup with 12 flights first, including an SD-card pilot, a regular pilot, an interrupted file copy, and two concurrent telemetry sources.

## Live metadata sync between two laptops

For two laptops running their own local copies, start the app on each laptop with its own `FPV_DATA_DIR`. Set the peer URL on each machine and run the sync command whenever you want metadata exchanged:

```bash
# On laptop 2, replace the address with the admin laptop hotspot IP
FPV_PEER_URL=http://192.168.43.1:5050 FPV_LOCAL_URL=http://127.0.0.1:5050 python sync_peer.py
```

Run the same command from the other laptop with the URLs reversed. This exchanges flight statuses, pilot tags, sessions, and file metadata. It deliberately does **not** copy video or blackbox media during the session. You can move `data/blackbox` and `data/video` manually after the session as requested.

The video queue is generated after every fourth landed flight per pilot, based on the pilot's actual landed/transfer/complete count rather than the repetition label. This supports fast SD-card pilots that fly continuously.

# OpenPLC v3 container

Builds the upstream OpenPLC v3 runtime + Flask web UI, ships `tank_pid.st` under `/programs/`, and runs `bootstrap.sh` on every boot to try uploading and starting the program automatically via the web UI.

## First-run setup (one time)

The auto-bootstrap is best-effort. If it succeeds, Modbus/TCP listens on `:502` within ~10 seconds of container start, the tank PID program is active, and the experimental matrix can run end-to-end.

If the bootstrap fails (typically because the OpenPLC web UI HTML changed and `bootstrap.sh` can't extract the new program id), do this once:

1. Open the OpenPLC web UI at `http://localhost:8080/`
2. Log in with `openplc` / `openplc`
3. **Programs** → **Browse...** → select the `tank_pid.st` that lives at `/programs/tank_pid.st` inside the container, or upload from the host: `containers/plc/programs/tank_pid.st`
4. Fill description "tank_pid", click **Upload Program**
5. Click **Launch program** on the new entry
6. Wait for "Compilation finished successfully"
7. **Dashboard** → **Start PLC**

The OpenPLC SQLite database is mounted on the Docker volume `plc-data` (mounted at `/opt/openplc/webserver/`), so the loaded program survives container restarts. After this one-time setup, every subsequent `docker compose up` brings the PLC up with the program already loaded — provided you do **not** `docker compose down -v` (the `-v` wipes the volume).

## REST API alternative

OpenPLC also exposes a REST API on HTTPS `:8443` (`/api/v1/...`). It's documented at <https://www.openplcproject.com/docs/rest-api/> and can be used as a more reliable automation path than the web UI. Future versions of `bootstrap.sh` should migrate to it.

## Modbus mapping

See `programs/README.md` for the holding register / coil mapping exposed by `tank_pid.st`.

## Default credentials

| Service              | URL                         | User     | Pass     |
|----------------------|-----------------------------|----------|----------|
| Web UI               | http://localhost:8080/      | openplc  | openplc  |
| REST API (HTTPS)     | https://localhost:8443/api/ | openplc  | openplc  |
| Modbus/TCP slave     | localhost:502               | n/a      | n/a      |

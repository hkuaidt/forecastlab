# Group8 service supervision

`supervisor-control.sh install` installs and starts only the group8 user unit
`forecastlab-supervisor.service`. It enables linger only for the group8 account,
so the user service can recover after logout and boot. It does not install a
system-wide service or change shared Docker/driver settings. Healthy existing
API, router and model processes are adopted without restarting them.

The supervisor checks every 10 seconds:

- API `/api/live` liveness. Model readiness is checked independently; unavailable
  workers do not cause an otherwise responsive API to restart.
- Router `/health` responsiveness. A valid 503 response with unavailable workers
  is still a live router; the worker recovery path handles model availability.
- Every worker's owner label, physical PCI/device mapping, explicit
  `MTHREADS_VISIBLE_DEVICES`, logical device 0 and loopback port, then `/health`.

An API/router failure lasting 15 seconds triggers its guarded controller, with a
30-second retry backoff. A worker health failure lasting 180 seconds triggers a
restart of that verified group8 container and launches the real vLLM process;
it does not merely restart the `sleep infinity` container. Worker recovery has a
180-second backoff. Healthy busy workers are not restarted. Starting a worker
still refuses to allocate when the allowed physical card has occupied memory.
The only eligible physical GPUs are 4, 5, 6 and 7.

Use the maintenance pause before intentional stops, data maintenance or rollback:

```sh
./deploy/supervisor-control.sh pause
# Perform the coordinated group8 maintenance.
./deploy/supervisor-control.sh resume
./deploy/supervisor-control.sh status
```

Pause leaves the API/router/workers running and stops the supervisor after its
current bounded operation. The maintenance marker persists across service or
host restarts until `resume`. Existing controller scripts remain available for
coordinated maintenance. API stop first verifies ownership of the loopback
listener, requests cancellation of queued/running records and waits up to four
seconds for cooperative drain before TERM. The helper has an outer five-second
limit; unavailable HTTP falls back to the existing guarded TERM/KILL stop.
This gives cancelled model calls a chance to close sockets and persist their
ledger before uvicorn waits for background work. If a TP4 service is restored in the future, first stop all four TP1 workers
and verify that their GPU memory has been released before starting TP4. The
original TP4 container no longer exists; recovery requires an explicit reviewed
recreation, not merely starting a retained container.

State is written atomically to `.forecastlab/supervisor-state.json`. Supervisor
logs rotate at 2 MB with three backups. Logs contain only component names,
physical GPU numbers and error classes, not environment values or prompts.
The user service restarts the supervisor itself after an unexpected exit.

Model requests share one absolute deadline across tokenization, router queueing,
response transfer, schema retries and network backoff. It is the earlier of
`FORECASTLAB_MODEL_TIMEOUT` (per `complete()` call) and remaining run activity
budget. Cancellation closes the asynchronous HTTP connection; it does not leave
an abandoned generation thread. The router propagates this disconnection to its
worker connection. CPU tests cover real loopback socket closure, slow header/body
responses, retry backoff, tokenization and remaining run budget. They do not send
model inference or measure GPU abort latency.

CPU recovery tests mock Docker/GPU operations and verify strict ownership,
visibility, recovery grace/backoff, maintenance pause and liveness/readiness
separation. Production fault injection must be coordinated when no research is
running; the test suite never kills a real model process.

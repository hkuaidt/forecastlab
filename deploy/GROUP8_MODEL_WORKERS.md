# Group8 GPU model workers

The current deployment uses four Qwen3-8B BF16 TP1 workers. All retain a 16,384
context window and thinking support. Application requests control the output
budget and thinking flag. Every container exposes only one allowed physical GPU;
its logical GPU is 0.

| Physical GPU | PCI address | Loopback endpoint | Container |
|---|---|---|---|
| 4 | 0000:32:00.0 | http://127.0.0.1:18054 | group8-qwen-tp1-gpu4-perf |
| 5 | 0000:38:00.0 | http://127.0.0.1:18055 | group8-qwen-tp1-gpu5-perf |
| 6 | 0000:3b:00.0 | http://127.0.0.1:18056 | group8-qwen-tp1-gpu6-perf |
| 7 | 0000:3c:00.0 | http://127.0.0.1:18057 | group8-qwen-tp1-gpu7-perf |

The model router owns port 18048 and admits one generation per worker. Keep the
worker endpoints on loopback; application traffic should use the router.

From the group8 server checkout:

```sh
python3 deploy/group8_model_workers.py status
python3 deploy/group8_model_workers.py start
```

`start` is idempotent for healthy workers. It validates owner labels, physical
PCI/device mappings, visible-device environment and loopback port bindings.
It will not stop processes, duplicate a live model server or allocate on an
occupied card. If a model process exists but is unhealthy, inspect its log and
drain application/router requests before any recovery. The script only accepts
physical GPUs 4, 5, 6, 7, optionally selected with `--gpus`.

The existing image and read-only driver mounts are recorded in
`deploy/group8-worker-runtime.json`. Recreating a worker no longer depends on
the original TP4 container; the manifest is restricted to reviewed read-only
driver files and the group8 model directory. Model weights and logs are under
`/home/group8/work/qwen-text`, including `logs/inference-tp1-gpuN-perf.log`.

## Measured behavior

Comparable 2026-10-08 microbenchmarks used BF16, 16k context, thinking=true,
829-token short or 7,726-token long input, and exactly 256 generated tokens
including reasoning. Four distributed TP1 requests completed in 10.70 seconds
short (95.70 aggregate tokens/s) and 25.61 seconds long (39.99 aggregate tokens/s).
One TP1 batching four long requests took 59.94 seconds (17.08 aggregate tokens/s).
Single TP1 remained about 24 tokens/s short and 10 tokens/s long.

The original TP4 was faster on short input (121 aggregate tokens/s at four
concurrent requests) and single requests (33.50 short / 11.34 long), but long
same-instance concurrent requests stalled. TP2 likewise stalled on long batches,
and its single long request was only 11.06 tokens/s. Therefore the change targets
stable independent multi-agent concurrency; it does not claim faster sequential
generation. Prefixes were warmed in the final comparison. Full workflow latency
also includes fresh prefill, variable output length, validation and retries.

Full reports, exact prompts/hashes, new generated outputs, device inventories and
original startup commands are retained in:
`/home/group8/work/forecastlab-ux-perf-20261008`.
Fixed 256-token microbenchmarks do not measure final report quality.

## Rollback

The original containers were deleted by an unidentified caller at 2026-10-09
00:37:24 CST. Four isolated TP1 workers were recreated from the saved configuration.
The old `rollback-tp4.sh` additionally requires the original TP4 container to exist;
it must not be advertised as a currently executable rollback until that container
is explicitly restored. The saved script refuses to run while an
application run is active or port 18048 is occupied. Drain the application and
router, then coordinate stopping the router before invoking rollback. It checks
all candidate containers are owned by group8 and restricted to allowed physical
GPUs before stopping TP1/TP2 workers and starting a separately restored TP4 only after confirming GPU memory is released.
No shared driver, host setting or other account's service is involved.

Account-local supervision and maintenance controls are documented in
[GROUP8_SUPERVISION.md](GROUP8_SUPERVISION.md).

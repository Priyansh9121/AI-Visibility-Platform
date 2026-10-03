# Design note: moving the browser work into a Render background worker

**Status: a note, not built.** Written 2026-10-02 for the founder's brief "memory and the
rest of the open decisions" (item M1). Nothing here is decided; it says what the move
would involve so it can be weighed against a bigger API instance.

## Why consider it

The API process runs Chromium itself, in three places: the intake crawl when a client is
added (`crawl_site`, inside the request), the scan's technical audit (phase 6,
`audit_site`), and an on-demand audit (`POST /scans/{scanId}/audit`). Measured in the
API's own Docker image on Linux (cgroup v2, `docker run --memory=…`, arm64 under Colima)
on 2026-10-02, before M1 (`471a2b9`) and with M1's final code. "Process memory" is the cgroup's
`anon`, what the kernel's OOM killer weighs; "charged" is `memory.current`, which also
counts page cache the kernel can drop. The heavy page is a synthetic local page (twelve
1600×1000 images, 220 sections, four cross-site frames, a script that allocates); the
light one is example.com.

| What | Before M1 | After M1 |
|---|---|---|
| The API server idle (the image's own entrypoint, warmed with 100 requests) | 97 MB process, 103–158 MB charged | 97 MB process, 103–104 MB charged |
| Crawl of the heavy page | 290–312 MB process | 290–319 MB process |
| Technical audit of the heavy page (images load, for LCP) | 455–489 MB process | 463–492 MB process |
| Crawl or audit of example.com | 230–248 MB process | 225–254 MB process |
| Crawl and audit started together, 2 GB limit | 586 MB process, 669 MB charged at peak | 482–492 MB process, 579–607 MB charged at peak (they run one after the other) |
| Crawl and audit started together, 512 MB limit | the kernel killed a Chromium process; the audit ended `BROWSER_ERROR` | both finished (463–476 MB process) |
| Chromium processes left after each run | two per crawl, two per audit (exited, never reaped) | none |

Browser memory is the page's own weight (decoded images, the DOM, its JavaScript), not
browser overhead: Chromium switches such as `--renderer-process-limit=1`,
`--disable-site-isolation-trials`, `--disable-gpu` and `--no-zygote` changed nothing
measurable, so none was adopted. So the API's peak follows whatever site a client points
it at, and a heavy site can take the API process down with every request in flight (the
512 MB Starter instance did, at 14:45 AEST on 2026-10-02, in the phase after the engines:
the audit).

M1 bounds this inside the API: one browser per process at a time (so the peak is one
browser run, about 0.4 GB above the server's own memory, however many scans and intakes
meet), a cap on each run, a bound on the wait for the browser (a request that waits 60 s
is refused with a `429` and writes nothing; a scan's audit that waits 120 s ends
`BROWSER_BUSY` and the scan goes on), one site read per agency at a time and four
agencies at once on the three routes that start one inside a request (more is a `429`),
`tini` as PID 1 to reap Chromium's exited processes, memory logged per phase, and the
Standard (2 GB) instance. On 2 GB that leaves about 1.4 GB free at the worst measured
moment, even if the server itself has grown to about 200 MB as it had on Render. The
wait counts against the scan's 40-minute deadline: the modelled worst case is now
2,064.5 s of 2,400 s (×1.16). A worker would remove the browser from the API instead.

## The shape

- **A Render Background Worker** built from the same repository, running the browser
  work only. `apps/workers` and the Celery broker and result backend (Redis db 1 and 2)
  already exist; the browser tasks would be two Celery tasks, `crawl` and `audit`.
- **Facts cross, page text does not.** The audit already returns facts only
  (`AuditSignals`). The crawl also returns `text_extract`, up to 18,000 characters of the
  site's own text, which intake classification sends to the model. Passing that through
  Redis would store third-party text in the broker and the result backend, which the
  facts-only rule forbids. So the worker must run the classification step too and return
  only its facts (business type, confidence, codes), or the crawl must stay where the
  classification runs.
- **The egress guard moves with the code**, unchanged. Only the worker would need to
  reach arbitrary websites, which makes 3.2's network-level egress control simpler: it
  would apply to the worker service alone, and the API could have no general outbound
  access beyond the engines and Stripe.
- **The API side.** `audit_runner.run_audit` enqueues and waits for the result with a
  timeout inside the scan's existing 40-minute deadline; a worker failure or timeout
  records the audit outcome the code already has (`BROWSER_ERROR` / `TIMEOUT`, or
  `BROWSER_BUSY` when the queue is too long), and the scan continues as it does today.
  The intake crawl either keeps the request waiting (as now, with a timeout) or becomes
  asynchronous: the client is created, classification is pending, and the web polls.
  The second is a product change.
- **Concurrency.** One browser per worker instance (Celery concurrency 1); more
  throughput means more instances. Queue depth shows load.
- **Sizes.** The API could return to a small instance (about 100 MB of process memory at
  rest, before the growth seen on Render); the worker needs about 0.5 GB of process
  memory at peak for a heavy page (0.6–0.8 GB charged with page cache), so a 1 GB
  instance at least, 2 GB for headroom.

## What it would take

1. A worker service on Render (another monthly cost) with the same image, started as a
   Celery worker for a `browser` queue.
2. The two tasks, with results as the existing facts-only dataclasses; classification
   moved into the crawl task.
3. A switch, `BROWSER_WORK=in_process|worker`, so local development and the tests keep
   the in-process path; ship the worker, then flip the switch on Render.
4. Retries: none by default (a failed audit is an ordinary outcome); check the Celery
   retry settings, which an earlier pilot found may be inert.
5. Logs: the same `browser.memory` lines, now from the worker.

## Open for the founder

- Whether the extra service is worth it at pilot volume, compared with keeping the API on
  2 GB (the M1 numbers: one heavy audit at about 0.5 GB of process memory under a 2 GB
  limit leaves wide headroom).
- Whether adding a client may become asynchronous in the web app (classification
  "pending"), which the worker makes natural.
- The scan deadline (settled for now, launch checklist 3.35): the scan audit's wait for
  the browser was shortened to 120 s, so with the browser wait inside the API the
  modelled worst-case scan uses 2,064.5 s of its 2,400 s (×1.16; at 300 s it was
  2,244.5 s and ×1.07, with launch checklist 3.41's 60 s pre-browser budget counted
  whole). A worker's queue wait would replace that wait.

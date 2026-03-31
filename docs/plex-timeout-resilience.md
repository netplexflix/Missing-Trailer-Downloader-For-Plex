# Plex Timeout Resilience

## Problem

When Plex is under load (busy scanning, deleting collections, serving streams), API calls from
mtdp can time out. Previously, any `ReadTimeout` or `ConnectionError` raised by `movie.reload()`,
`movie.extras()`, `show.reload()`, or `show.extras()` would propagate up uncaught and crash the
entire run mid-library. Progress was lost and the process had to restart from scratch (minus any
items that had been successfully labelled before the crash).

Example crash:

```
Checking movie 288/6655: The Amateur
...
requests.exceptions.ReadTimeout: HTTPConnectionPool(host='...', port=32400):
Read timed out. (read timeout=30)
```

## Fix

A `plex_call()` helper wraps every Plex API call in the main processing loops with retry logic
and exponential-style backoff:

```python
PLEX_RETRY_ATTEMPTS = 5
PLEX_RETRY_DELAY = 15  # seconds between retries

def plex_call(fn, *args, label="Plex API call", **kwargs):
    for attempt in range(1, PLEX_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            if attempt < PLEX_RETRY_ATTEMPTS:
                print_colored(
                    f"  {label} timed out (attempt {attempt}/{PLEX_RETRY_ATTEMPTS}), "
                    f"retrying in {PLEX_RETRY_DELAY}s...", 'yellow'
                )
                time.sleep(PLEX_RETRY_DELAY)
            else:
                print_colored(f"  {label} failed after {PLEX_RETRY_ATTEMPTS} attempts: {e}", 'red')
                return None
        except Exception as e:
            print_colored(f"  {label} unexpected error: {e}", 'red')
            return None
```

### Behaviour after the fix

| Scenario | Before | After |
|---|---|---|
| Single timeout on `reload()` | Crash — run aborted | Retries up to 5× with 15s delay |
| Persistent Plex unavailability | Crash — run aborted | Skips that item, continues with next |
| Timeout on `extras()` | Crash — run aborted | Retries, skips item on persistent failure |
| Label write failure | Logged, run continues | Unchanged (already non-fatal) |

### Files changed

- `Modules/Movies.py` — added `plex_call()`, replaced bare `movie.reload()` and `movie.extras()` calls
- `Modules/TV.py` — same changes for `show.reload()` and `show.extras()`

### New imports

```python
import time
import requests.exceptions
```

Both are already present in the dependency tree (`requests` is a `plexapi` dependency).

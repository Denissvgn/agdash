# Backfill performance

How long agdash takes to read a harness's history, and how much memory it
needs to do it. The numbers come from `backend/benchmarks/backfill.py`, which
runs the same collector the service runs.

```bash
(cd backend && ../.venv/bin/python benchmarks/backfill.py synthetic --sizes 5,20,80,160)
(cd backend && ../.venv/bin/python benchmarks/backfill.py measure /path/to/a/home)
```

`synthetic` builds Codex rollouts of each size from the synthetic test fixture,
once as a plain file and once as a compressed archive, and backfills each in a
fresh process. `measure` backfills the Codex files under a home folder and
prints only counts, sizes, time and peak memory, never content.

Measured on an Apple M5 Pro with 24 GB of memory, macOS 27.2, Python 3.14.

## A real Codex archive

| | |
|---|---|
| Files | 1,177 rollouts: 220 plain, 957 compressed |
| Size | 2.09 GB on disk, 3.0 GB of transcript once decompressed |
| Lines read | 929,193 |
| Time | 371 s, about 8 MB of transcript a second |
| Peak memory | 277 MB for the whole backfill |
| Store | 2.6 GB, including the compressed raw-line archive |

The largest plain rollout on its own, 555 MB, peaks at 217 MB.

## Peak memory against transcript size

| Rollout | 5 MB | 20 MB | 80 MB | 160 MB |
|---|---|---|---|---|
| Plain | 141 MB | 172 MB | 171 MB | 170 MB |
| Compressed | 152 MB | 204 MB | 219 MB | 227 MB |

Plain files are read in 8 MB batches and stay flat. Compressed archives are
streamed the same way; their peak rises by about 23 MB across an eightfold range,
then levels off. Each run's figure includes the process itself, around 100 MB
with the store and its libraries loaded.

## What makes it bounded

- **Batches.** Plain files are read 8 MB at a time. Compressed archives are first
  read through once to prove they are complete, keeping nothing, then decompressed
  again and stored in batches of the same size. A read cut short resumes after the
  last stored batch.
- **No growing buffers.** Decompressed pieces are collected and joined once per
  batch, and the raw-line archive compresses each batch as a stream. Growing one
  buffer piece by piece left freed memory with the process, which raised the peak
  to about 750 MB on the real archive.
- **Bounded decompression.** The decoder is fed a kilobyte of input at a time, so
  a highly compressed file cannot expand by more than a fixed amount per step. An
  archive that decompresses to more than 16 GB is refused.

## What makes it fast

Synthetic throughput went from 1.8 MB a second to 8 MB a second.

- **Redaction** runs each pattern only when a cheap test that every match needs
  passes, such as a keyword or a token prefix. Text without such a sign skips the
  costly patterns. The result is identical; tests compare it with running every
  pattern on the secret corpus, every fixture line and random mixes.
- **Event validation** checks each event only against the rules for its own kind,
  which gives the same verdict as the whole schema.
- **Stored events** are looked up a window of the file at a time instead of with
  one query per event.

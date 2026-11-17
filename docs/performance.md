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

## What shows while it runs

The dashboard does not wait for a backfill. When the service starts, it reads
the files of sessions that have a process now first, the smallest first, and
then the rest from the newest to the oldest. It reads in slices of about a
second, and between slices it reads whatever the harnesses wrote meanwhile, so
a running session keeps up while history is still coming in. The Live board and
History say how many files are read of how many until it is done.

Only the folders that can hold session files are listed and watched: a
harness's checkouts and installed packages beside them are never walked. A
changed file is looked up directly, and a full pass over everything, every 30
seconds, reads only the files whose size or modification time changed.

On a home with 6,383 session files (7.4 GB of transcripts) beside 120,000 other
files in the harness folders:

| | |
|---|---|
| First live session on the board, first ever start | 6 s |
| All eight running sessions on the board | 30 s |
| Listing the session files | 0.6 s |
| A restart catching up with every file | 9 s |
| A line written by a harness reaching the store | 0.3 s (median) |

A harness that writes its records late adds its own delay on top: Codex writes
in bursts, many seconds after the events they record.

## What it costs to keep watching

Once the backfill is done the service keeps three things going: it looks at
the process table every two seconds, reads what the harnesses write as they
write it, and every 30 seconds checks every session file against the listing.

On the same home, with a store of 7.7 GB (1.7 million events, 7,700 sessions),
about 500 processes of the user and a dozen sessions running, measured over two
minutes with no dashboard window open:

| | |
|---|---|
| Processor, average | 3.7% of one core |
| Processor, the few seconds of a 30-second check | 10 to 17% of one core |
| Memory | 59 MB |
| Write-ahead log on disk | at most 64 MB |

What keeps it there:

- A pass never reads every session or every event. It asks for the sessions
  that are live or lately active by index, and a dashboard's live stream reads
  only the events newer than the last it saw.
- A process is asked for its program and arguments when it is first seen,
  while it is new, when its name changes, and otherwise once in two minutes.
- The 30-second check takes each file's size and time from the directory
  listing, and asks nothing about files that cannot be session files.
- Antigravity's conversation summaries are stored again only when their file
  has changed.

The service measures this itself. Every ten seconds it asks the operating
system about its own process: processor time in all and for each of its
threads, memory, and the bytes it read from and wrote to storage. `agdash
usage` prints the current figures, and Settings · Service shows them over the
last hour, day or 90 days, with the processor time each part of the service
took. The memory it shows is the footprint, which counts memory the system has
compressed or swapped out; resident size alone leaves that out and reads
several times lower on a machine under memory pressure. One row a minute is
kept for two days and one an hour for ninety, under a megabyte in all, and a
sample costs well under a millisecond.

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

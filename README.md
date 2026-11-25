# Cool Edit `.ses` → REAPER `.rpp`

Convert legacy Cool Edit / Audition multitrack sessions (`.ses`) into REAPER project files (`.rpp`).

The converter is a single Python script (`ses2rpp.py`) and uses only the Python standard library.

## Quick Start

```bash
cd /home/user/code/cool-reap
python3 ses2rpp.py "/path/to/Session.ses"
python3 -m unittest discover -v
```

This writes `/path/to/Session.rpp` beside the input `.ses`.

## Requirements

- Python 3.8+
- (Optional) REAPER CLI (`reaper`) if you want to validate rendered output

No third-party Python packages are required.

---

## Optional: create a virtual environment

You do **not** need a venv for this script, but here are clean setup steps if you want one:

```bash
cd /home/user/code/cool-reap
python3 -m venv .venv
source .venv/bin/activate
python -V
```

To leave the venv later:

```bash
deactivate
```

---

## Basic usage

From the `cool-reap` directory:

```bash
python3 ses2rpp.py "/path/to/Session.ses"
```

By default, output is written **next to the source `.ses`** with the same stem:

- `/path/to/Session.ses` → `/path/to/Session.rpp`

You can convert multiple sessions in one command:

```bash
python3 ses2rpp.py \
  "/home/user/Music/song1/Untitled.ses" \
  "/home/user/Music/song2/Untitled.ses" \
  "/home/user/Music/song3/Untitled.ses"
```

---

## CLI options

```text
python3 ses2rpp.py [options] file1.ses [file2.ses ...]

Options:
  -o, --out PATH      Output path (single input only)
  --media-dir DIR     Extra directory to search for media by basename
  --strict            Fail conversion if any media file is unresolved
  --no-eq             Do not emit ReaEQ for track EQ
  --no-master         Ignore hdr master gain fields
  --debug             Print decoded chunk/track/clip/media counts
```

### Media relinking behavior

For each clip source path, resolver order is:

1. File with same basename beside the `.ses`
2. File with same basename in `--media-dir` (if provided)
3. Keep original path in project and emit a warning

Warnings are printed as:

```text
warning: unresolved media: ...
```

If `--strict` is enabled, unresolved media causes exit code `1` and no `.rpp` is written.

---

## Run tests

```bash
cd /home/user/code/cool-reap
python3 -m unittest discover -v
```

---

## Validate generated projects with REAPER (optional)

Dry-run peak check:

```bash
reaper -nosplash -newinst -peaktest "/path/to/Session.rpp"
```

Render project:

```bash
reaper -nosplash -newinst -renderproject "/path/to/Session.rpp"
```

---

## What is preserved

- Tracks and track names
- Track level/pan (reconstructed from stored L/R gains)
- Clips/items: position, length, source offset, source file mapping
- Clip static gain and non-identity clip volume/pan envelopes (`ep20`)
- Tempo/time-signature mapping from `tmpo`
- Markers/regions (`cues`)
- Track EQ (`tkeq`) mapped to ReaEQ

## What is not fully covered

- Arbitrary non-EQ effects chains
- MIDI/instrument data
- Surround and advanced routing behaviors
- Unknown/unsupported proprietary chunk variants

Unsupported or ambiguous data is surfaced as warnings instead of silently dropped.

## License

MIT — see [`LICENSE`](./LICENSE).

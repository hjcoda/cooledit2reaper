# Cool Edit `.ses` → REAPER `.rpp` converter — design

Date: 2026-11-24
Status: proposed, awaiting approval
Project: `/home/user/code/cool-reap`

## Intent

Convert legacy Cool Edit Pro / Audition binary `.ses` multitrack sessions into
REAPER `.rpp` projects without needing Cool Edit, Audition, or any external
package. The immediate targets are the three sessions in
`/home/user/Music`:

- `Scared In Amsterdam/Untitled.ses` — 23 tracks, 44 clips
- `oncve again/Untitled.ses` — 12 tracks, 17 clips
- `My muse/Untitled.ses` — 19 tracks, 20 clips

The converter must preserve the timeline, track layout, names, levels, pans,
clip offsets/gain, track EQ, tempo, and markers. It must not silently drop
mix-affecting data: anything it cannot convert is reported.

## Goals

- Single self-contained Python 3 script, standard library only.
- Direct `.ses` → `.rpp` conversion (no `.sesx` intermediate).
- Reproduce the three target sessions faithfully enough to open and play in
  REAPER with the same arrangement, clip placement, and static mix.
- Relink Windows media paths to files beside the `.ses`, with overrides.
- Warn loudly (or fail with `--strict`) for unresolved media and unsupported
  active features.
- Validate by loading/rendering generated projects with the installed REAPER.

## Non-goals

- Converting Cool Edit effects/plugins, MIDI, waveform edits, or surround.
- Reproducing Cool Edit's exact EQ/floating-point signal path.
- Converting Audition's XML `.sesx` format.
- Modifying the source `.ses` files.

## Input format notes (reverse-engineered)

All findings below were measured from the three real sessions, not decompiled
from Cool Edit. The container is undocumented; record sizes differ between
Cool Edit Pro 2.x and later Audition 1.x. The converter must fail cleanly on
layouts it does not recognise.

### Container

- Magic: `b"COOLNESS"` (8 bytes).
- Next: `u32` total size after the first 12 bytes (informational).
- Chunks start at offset 12: `4-byte ASCII id`, `u32 LE body length`, body.
- Special case: the 8-byte tag `LISTFILE` is followed by `u32 length`, then body.
- Chunks are back-to-back; tolerate a single pad byte between chunks.
- First occurrence of each chunk id wins.

### Chunks used

| Chunk | Meaning |
|---|---|
| `hdr ` | Session header. `u32` sample rate at body+0 (44100 in all three). Two equal `f64` values at body+16/body+24 appear to be master L/R gain; treat as master fader but keep behind a flag until verified. |
| `tmpo` | Tempo. `f64` BPM at +0 (100.0), `u32` beats/bar at +8 (4), `u32` subdivisions at +12 (16). These files are 4/4. |
| `stat` | Timeline/selection state. Not required for the RPP. |
| `trks` | Track table (see below). |
| `LISTFILE` | Media file list. Scroll only the first `LISTFILE` body for `wav ` records. |
| `bk20` | Secondary clip table; `u32 count`, `u32 recsize` (72), records. Carries loop extent at record+28 and source-in at +32. |
| `tkeq` | Track EQ table (see below). |
| `blk ` | Primary clip table (see below). |
| `blk2` | Clip-handle map; record order matches `blk ` order. Used to map `ep20` envelopes to clips. |
| `ep20` | Per-clip parameter envelopes (volume/pan). |
| `envp` | Per-clip parameter envelopes, second representation. |
| `loop` | Clip handles of looped clips (`u32` values). |
| `cues` | Markers/regions. `u32 count`, then 24-byte records: `i32 position` +0, `i32 duration` +4 (0 = point), name +12. |
| `mtro`, `midx` | Metronome/index data; ignored. |

Anything else (e.g. `tkfx`, `buss`, `MCHN`) is unsupported; warn if it appears
to affect the mix.

### Track records (`trks`)

Two known layouts:

**Cool Edit Pro 2.x (present in all three files):**

- `u32 count` at body+0.
- Records start at body+4, size **96 bytes**.
- `f64 leftGain` at +0 (linear; 1.0 = unity).
- `f64 rightGain` at +8.
- `u32 flags` at +16. Observed values: 0, and 4 on the last track only.
  Do not map this to mute/solo without evidence; it may be selection/height.
- 40-byte NUL-terminated Latin-1 name at +20.
- Remaining fields are per-record hashes/defaults and are ignored.

**Audition 1.x:**

- No count word.
- Records start at body+0, size **152 bytes**.
- `f64 volume` at +4, `i32 mute` at +20, name at +24 (40 bytes).
- (Documented by the MIT `ses2sesx` project; no sample of this layout was
  available locally, so this path is best-effort and must be covered by
  synthetic tests.)

**Detection:** choose the 96-byte layout when
`(len(body) - 4) % 96 == 0` and `u32(body, 0) == (len(body) - 4) // 96`;
otherwise choose the 152-byte layout when `len(body) % 152 == 0`; otherwise
report an unsupported track table.

**Fader/pan reconstruction:** Cool Edit stores post-pan left/right gains. The
observed values are either equal (centre) or one channel at/near zero (hard
pan), so the centre law is 0 dB (no attenuation):

- `volume = max(left, right)`
- `pan = 0` when `left ≈ right`
- `pan = -1` when `right ≪ left`
- `pan = +1` when `left ≪ right`
- intermediate: linear balance, `pan = 1 - left/right` for right pan and
  `pan = right/left - 1` for left pan, with `volume = max`.
- Emit the project with a **0 dB pan law** so the gains reproduce exactly.

### Clip records (`blk `)

- `u32 count` at body+0.
- Records start at body+8, size **88 bytes**.
- `f64` static clip gain at +0 (1.0 in these files; use if no envelope).
- `f64` at +8 (1.0; unused).
- `i32 timelineStart` at +28 (samples at session rate).
- `i32 length` at +32 (samples).
- `u32 handle` at +36.
- `i32 filePointer` at +44 → key into the media map.
- `i32 trackNumber` at +48 (1-based).
- `i32 sourceIn` at +60 (samples).

### Media map (`LISTFILE` + `wav `)

- Inside the first `LISTFILE` body, each `wav ` record has:
  - `u32 recordLength` at +4
  - `u32 filePointer` at +8
  - an absolute Windows path string in the body beginning at +8.
- Map `filePointer` → path. Prefer a drive-letter/UNC anchored path; fall back
  to a printable basename for loop-library sessions.
- Bound the scan to the first `LISTFILE` body so a `wav ` byte sequence in
  numeric data cannot produce a false record.

### Clip gain / pan envelopes

`ep20` layout:

- `u32 count` at +0.
- Per record: `u32 marker`, `u32 version`, `u32 handle`, `u32 groupCount`.
- Per group: `u32 pointCount`, `u32 parameterId`, `u32 reserved`.
- Per point: `u32 sampleOffset`, `f64 value`.
- Parameter 1 = linear clip volume; 2 = clip pan (0.5 = centre). Parameter 4
  was identity (1.0) in these sessions.
- Map record `handle` to clip via `blk2` (record order matches `blk ` order).

`envp` layout (second representation):

- `u32 count` at +0.
- Per record: 16-byte header (`u32` marker, `u32` handle, `u32 group0Count`,
  `u32 group1Count`), then group0 points (24 bytes each), then group1 points.
- Point: `u32 position` +0, `u32 pad` +4, `f64 value` +8, `u32` +16, `u32` +20.
- Group0 is clip volume, group1 is clip pan (per the MIT decoder).
- All three target sessions are identity; implement only if it is cheap, and
  otherwise warn and use `ep20`/static gain.

### Track EQ (`tkeq`)

- `u32 count` at +0; records at +4, size **88 bytes**.
- `u32 trackNumber` at +0 (0 = master).
- `f64 lowFrequency` at +8 (160.0).
- `f64 lowGainDb` at +16.
- `f64 highFrequency` at +24 (7000.0).
- `f64 midGainDb` at +32.
- `f64 midFrequency` at +40 (2500.0).
- `f64 q` at +48 (0.33).
- `f64 highGainDb` at +56.
- `f64` flags/defaults at +64/+72.
- Only emit EQ when a gain is non-zero.
- Map to ReaEQ: band 1 low shelf at lowFrequency/lowGainDb; band 2 peak at
  midFrequency/midGainDb with bandwidth
  `2*asinh(1/(2Q))/ln(2)` octaves; band 3 high shelf at
  highFrequency/highGainDb. ReaEQ shelf bandwidth defaults to 2.0 octaves.
- ReaEQ state is a base64 VST chunk. Format (from the MIT REW-to-ReaEQ
  project): 48-byte signature, `u32 chunkSize`, 8-byte sub-header
  `01 00 00 00 00 00 10 00`, `u32 stride=33`, `u32 bandCount`, then per band
  `i32 type, i32 enabled, f64 freq, f64 linearGain, f64 bandwidth, u8 flag`,
  then `i32 1, i32 1, f64 1.0`. Type 0 = low shelf, 1 = high shelf,
  8 = peak/band.

## Output mapping (`.rpp`)

- Project header: `<REAPER_PROJECT 0.1 ...>`, `SAMPLERATE`, `PANLAW 0`,
  `PANLAWFLAGS 3`, `PANMODE 3`, `TEMPO bpm 4 4`, master volume from `hdr`
  (behind `--no-master` if verification shows it is not a fader).
- One `<TRACK>` per track in `trks`, including empty tracks.
- Track fields: `NAME`, `VOLPAN volume pan -1 -1 1`, `MUTESOLO`,
  `NCHAN 2`; ReaEQ in `<FXCHAIN>` when the track has non-zero EQ.
- One `<ITEM>` per clip: `POSITION`, `LENGTH`, `SOFFS`, `LOOP`,
  `VOLPAN` (static clip gain), `MUTE`, `NAME`, and
  `<SOURCE MP3|WAVE>` with `FILE`.
- Clip volume/pan automation becomes item/take envelopes; static non-unity
  gain goes in item `VOLPAN`.
- Loop clips use the `bk20` on-timeline extent and `LOOP 1`.
- Cues become `MARKER`/region records; durations > 0 produce regions.
- Media resolution:
  1. use `--media-dir` when supplied;
  2. otherwise use the file with the same basename beside the `.ses`;
  3. otherwise keep the original Windows path and warn;
  4. `--strict` turns unresolved media into an error.
- Missing files remain as offline items by default, so the arrangement is not
  silently changed.

## CLI

```
python ses2rpp.py INPUT.ses [INPUT2.ses ...] [-o OUT.rpp]
                   [--media-dir DIR] [--no-eq] [--no-master]
                   [--strict] [--quiet] [--debug]
```

- Single input default: `<stem>.rpp` beside the input.
- Multiple inputs: one `.rpp` per input; `-o` is rejected.
- `--debug` prints decoded chunks/tracks/clips/media for troubleshooting.

## Validation plan

1. Decode all three sessions and assert counts:
   - 23/44, 12/17, 19/20 tracks/clips.
   - 44.1 kHz, 100 BPM.
   - every clip resolves except the one reference mix per session.
2. Generate RPPs for all three and render them with the installed REAPER
   (`reaper -renderproject`) to prove they load, resolve the local MP3s, and
   produce non-silent audio.
3. Parse the generated RPPs back and assert track/item counts, positions,
   lengths, source offsets, and media paths match the decoded `.ses` values.
4. Synthetic tests for the 152-byte Audition track layout and empty/missing
   chunks, so the code fails cleanly instead of crashing.

## Risks / open questions

- `hdr` f64 fields: assumed master L/R gain. If wrong, disable master mapping.
- CEP2 `flags` field: not mapped to mute/solo because no muted track was
  available to compare. Default is unmuted; `--debug` exposes it.
- Some sessions may store media without an absolute path; basename relinking is
  the fallback.
- MP3 source offsets are sample-accurate in Cool Edit; REAPER should honour
  `SOFFS`, but the render check only proves the file loads and plays.
- The track EQ in Cool Edit may use different filter shapes than ReaEQ. The
  conversion is a close approximation, not a bit-exact match; it is still
  better than dropping an active +10 dB shelf.

## Decisions made

- Direct conversion rather than the `.sesx` intermediate.
- 0 dB pan law and `volume = max(L,R)` to reproduce Cool Edit's stored L/R
  gains.
- Track EQ maps to ReaEQ; all-zero EQ is omitted.
- Missing media stays as an offline item unless `--strict`.
- Outputs are written beside the input `.ses` by default.

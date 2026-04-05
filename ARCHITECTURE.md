# Architecture

## MKA as transparent storage

Yoto Library uses MKA (Matroska Audio) as its internal storage format. Each track is a single `.mka` file containing:

```
track.mka
├── audio stream    — original codec, untouched (AAC, MP3, FLAC, etc.)
├── Matroska tags   — TITLE, ARTIST, YOTO_SOURCE_FORMAT, etc.
├── icon attachment  — PNG or GIF, named "icon"
└── source.patch    — bsdiff patch for byte-perfect export (optional)
```

MKA is chosen because Matroska supports arbitrary metadata tags, file attachments, and any audio codec — all in a single container with standard tooling (mkvtoolnix, ffmpeg).

## Transparent middleman principle

MKA is never exposed to external systems. It exists solely as enriched local storage:

- **Yoto gets native audio.** On upload, `extract_audio()` remuxes the MKA back to its native container (e.g., `.m4a` for AAC) via `ffmpeg -c copy`. Yoto's transcode API receives exactly the same file it would get from a direct upload.

- **Export gives back the original.** `yoto export` extracts audio and applies a stored `bsdiff` patch to reconstruct the original file byte-for-byte.

```
Import:  original.m4a ──wrap──> track.mka + source.patch
Upload:  track.mka ──extract──> track.m4a ──upload──> Yoto API
Export:  track.mka ──extract──> track.m4a ──bspatch──> original.m4a
```

## Source format tag

Every MKA stores a `YOTO_SOURCE_FORMAT` tag (e.g., `m4a`, `mp3`, `flac`) recording the original file's container format. This is used by `extract_audio()` to choose the correct output format without needing to probe the codec.

## Binary diff/patch (bsdiff)

At import time, the pipeline:

1. Wraps the source file in MKA
2. Extracts it back via `ffmpeg -c copy` (deterministic reconstruction)
3. Computes `bsdiff(reconstructed, original)` — typically 50-100KB
4. Stores the patch as an MKA attachment named `source.patch`

The patch is computed against the **extracted audio stream**, not the MKA file itself. Since the audio stream never changes regardless of tag/icon edits to the MKA, the patch remains valid through the file's entire lifecycle.

**Dependencies:**
- `bsdiff` — required at import time for patch generation. Optional; if missing, import succeeds but export won't be byte-perfect. Install via `brew install bsdiff`.
- `bspatch` — required at export time. Ships with macOS (no install needed).

## Codec-to-container mapping

`extract_audio()` maps audio codecs to their native containers:

| Codec | Extension | ffmpeg format |
|-------|-----------|---------------|
| AAC   | .m4a      | ipod          |
| ALAC  | .m4a      | ipod          |
| MP3   | .mp3      | mp3           |
| Opus  | .ogg      | ogg           |
| Vorbis| .ogg      | ogg           |
| FLAC  | .flac     | flac          |
| PCM   | .wav      | wav           |

Unknown codecs fall back to MP3 transcoding as a safe default.

## Deterministic extraction (critical for bsdiff)

The bsdiff round-trip only works if `extract_audio()` produces **identical output** every time it's called on the same MKA, regardless of what metadata or attachments have been added since the patch was generated. This required solving three separate problems with ffmpeg's default behavior:

### Problem 1: MKA attachments are treated as video streams

When an MKA file has attachments (icon PNGs, the source.patch itself), ffmpeg sees them as additional streams. A PNG attachment becomes a "video" stream. Without explicit stream selection, ffmpeg tries to copy the PNG into the output container (e.g., M4A), which fails because the M4A muxer can't handle image dimensions it doesn't know about:

```
Stream #0:2 -> #0:0 (copy)     ← PNG attachment
[ipod @ ...] dimensions not set
Could not write header: Invalid argument
```

**Fix:** `-map 0:a` — explicitly select only audio streams, ignoring attachments and any other non-audio data.

### Problem 2: MKA metadata leaks into extracted output

By default, ffmpeg copies container-level metadata (Matroska tags like TITLE, ARTIST) into the output file's metadata. This means adding or changing tags in the MKA produces a different extracted file — the audio bytes are the same, but the container metadata differs, changing the file's hash:

```
# After wrap + write_tags({source_format: m4a})
extract → 16,191 bytes, hash 6edcb74c...

# After write_tags({source_format: m4a, title: Test})
extract → 16,219 bytes, hash 7e7b5ec7...   ← different!
```

**Fix:** `-map_metadata -1` — strip all metadata from the output. The extracted file contains only the raw audio stream in its native container, making it independent of whatever tags are stored in the MKA.

### Problem 3: OGG muxer assigns random serial numbers

The OGG container format uses a serial number in its page headers. ffmpeg's OGG muxer generates a new random serial number on every invocation, so two extractions of the same audio produce different files:

```
extract_audio(mka, out1) → hash f4cb9131...
extract_audio(mka, out2) → hash 005e66da...  ← different serial number
```

**Fix:** `-fflags +bitexact` — forces ffmpeg to use deterministic values for fields that would otherwise be random (OGG serial numbers, MP4 creation timestamps, etc.).

### The full extraction command

All three fixes combine into the extraction command used by `extract_audio()`:

```
ffmpeg -y -i track.mka \
    -map 0:a \              # only audio streams (ignore attachments)
    -c copy \               # no re-encoding
    -map_metadata -1 \      # strip MKA metadata from output
    -fflags +bitexact \     # deterministic muxer output
    -f ipod track.m4a       # native container format
```

This produces byte-identical output regardless of what tags, icons, or other attachments have been added to the MKA since the bsdiff patch was generated.

## Upload pipeline

Yoto's transcode API accepts any audio format — including MKA — and transcodes to Opus/OGG for playback. MKA files are uploaded directly without extraction; the transcoder handles the Matroska container and finds the audio stream inside.

The content schema's `format` field must match the **transcoded output** format (`"opus"`), not the input format. Yoto hardware firmware v2.21.4+ supports Opus playback.

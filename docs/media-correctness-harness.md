# synthetic media correctness harness

The Studio acceptance test generates the existing local fixtures in a temporary directory, copies one under a Unicode/comma filename, and imports them through `Library.import_media`. It records hashes before import and checks the registered metadata and managed originals after the render. Generated sources, database, proxies and MP4 output are temporary and are not added to Git.

## reproduce

From the repository root with the documented environment configured:

```bash
.venv/bin/python -m unittest discover -s tests/studio -p 'test_media_acceptance.py' -v
./scripts/verify-studio
./scripts/verify
```

The focused test requires the repository's existing FFmpeg/ffprobe binaries and Python environment. It adds no dependencies. The two verification scripts include the Studio, browser and desktop-backend checks as defined in the scripts.

## content assertions

The acceptance timeline uses a `30000/1001` sequence and independently edited picture and sound tracks. It starts with the existing fractional-rate MOV fixture, whose audio stream starts at `1/4` second; a later source range comes from the black-to-white MP4 fixture, and a 550 Hz MP3 range is inserted onto the audio track. The decoded output is checked for:

- persisted source hashes, stream structure, rates and source offset;
- rendered MP4 stream types, channel count, duration and frame timestamps;
- complete FFmpeg decode, sampled decoded picture luma before and after the inserted cut, and decoded 440 Hz / 550 Hz tone frequencies;
- decoded silence in intentional audio gaps;
- unchanged source hashes and byte-identical stored JSON for the base and intermediate revisions, including after a rejected overlap;
- clear rejection of unsupported `.mkv` and malformed `.mp4` inputs without adding assets.

Decoded-output helpers in `tests/studio/media_acceptance.py` return measurements or include the path, sample interval, observed bytes/metrics and FFprobe details in assertion failures. FFmpeg errors retain the existing command wrapper's bounded stderr diagnostic.

The preview renderer encodes constant-frame-rate output at the canonical timeline rate. The acceptance test caught FFmpeg otherwise defaulting this composed output to 25 fps.

## observed verification

Run on 2026-11-25 with Python 3.14.2 and the repository's configured FFmpeg/ffprobe:

- The focused acceptance module passed both tests. The fractional-rate render contained both streams, retained a `30000/1001` frame cadence, decoded 60 picture frames, and passed sampled picture, tone, gap, hash and revision checks.
- `./scripts/verify-studio` passed all 20 Studio Python tests; one optional local speech test was skipped because its executable/model/fixture were not configured. Its browser phase did not pass: the `show b` click in `tests/studio/browser.mjs` is intercepted by the overlapping `accept b` button. Because the script stops at that failure, its desktop-backend phase was run separately and passed.
- `node tests/studio/desktop-backend.mjs` passed the local session authorization and shutdown checks.
- `./scripts/verify` passed `pip check`, all 5 root Python tests, and the root browser playback/seeking/range checks on Chrome 159.0.8154.6.

The Studio browser hit-target failure remains an unverified UI limitation; it is not hidden or replaced with a forced click. The media acceptance harness and backend tests pass independently of that UI flow.

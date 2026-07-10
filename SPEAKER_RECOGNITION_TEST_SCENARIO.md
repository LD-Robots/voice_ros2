# Speaker Recognition — Test Scenario

Covers the persistent, portable voiceprint implementation:
`speaker_scoring.py` (math), `speaker_embedding_store.py` (durable store),
`speaker_manager.py` (centroid enrollment + load/persist), `speaker_id_node.py`
(temporal smoothing + auto-enroll persistence).

Legend: **PASS** criteria are the exact log lines / file states to verify.

---

## A. Automated (no hardware, fast)

### A1. Unit tests
```bash
cd conversational_client
python3 -m pytest test/test_speaker_scoring.py test/test_speaker_embedding_store.py -q
```
**PASS:** all tests green (scoring math, centroid, smoother, store round-trip,
model-mismatch invalidation, atomic write).

### A2. Offline end-to-end: enroll → persist → recognize with NO wav
```bash
cd conversational_client
PYTHONPATH=. python3 - <<'PY'
import os, shutil, tempfile, numpy as np, soundfile as sf
from conversational_client.speaker_manager import SpeakerManager
SRC='../voices/enrollment'            # uses whatever clips/store exist
d=tempfile.mkdtemp()
# seed from the live store if present, else from a wav you drop in
for f in os.listdir(SRC):
    if f.endswith(('.wav','speaker_embeddings.json')):
        shutil.copy2(os.path.join(SRC,f), d)
m1=SpeakerManager(d); print('A: speakers',m1.get_speakers())
# wipe all audio, keep only the store -> simulate a fresh robot
for f in os.listdir(d):
    if f.endswith('.wav'): os.remove(os.path.join(d,f))
m2=SpeakerManager(d); print('B(no wav): speakers',m2.get_speakers())
assert m2.get_speakers(), 'FAIL: store did not load without wavs'
print('PASS: recognized from store alone')
PY
```
**PASS:** `B(no wav): speakers ['speaker_001', ...]` and `PASS:` line prints.

---

## B. Single-node live (ROS, mic), `/speaker_id` only

Run just the ID path and echo the topic:
```bash
ros2 run conversational_client speaker_id_node \
  --ros-args -p enrollment_dir:=$PWD/voices/enrollment &
ros2 topic echo /speaker_id
```
(You still need audio_capture → vad → audio_segment running to feed it; use the
full launch below if echoing in isolation is inconvenient.)

---

## C. Full system (Gemini) live scenarios

Launch:
```bash
ros2 launch conversational_server full_system.launch.py \
  config:=laptop conversation_backend:=gemini_live
```

### C1. Cold start with empty enrollment → Unknown mode
**Pre:** `voices/enrollment/` has no `.wav` and no `speaker_embeddings.json`
(move it aside).
**PASS:** `⚠️ No voiceprints yet (no .wav clips and no saved embeddings)` and
every segment publishes `/speaker_id: "Unknown"`.

### C2. Auto-enroll via self-introduction → store created + name saved
**Pre:** empty enrollment (as C1). Say wake word, then speak a **recognized
self-introduction** (>1.2s, within 8s):
> "My name is Vasile."   (or RO: "Mă numesc Vasile.")

Accepted patterns: `my name is X`, `call me X`, `i am called X`, `i go by X`,
`mă numesc X`, `mă cheamă X`, `numele meu este X`.
**PASS (logs):**
- `person_memory_store_node: Requested automatic speaker enrollment for "Vasile"`
- `speaker_id_node: ✅ Auto-enrolled speaker "Vasile" as speaker_001`
- `person_memory_store_node: Automatic enrollment completed for "Vasile" as speaker_001`

**PASS (files):**
- `voices/enrollment/speaker_embeddings.json` now contains `speaker_001`
- `voices/person_memory.json` → `speaker_001.preferred_name == "Vasile"`

> Negative check: saying "Vasile, by the way" or "tell me my name" must NOT
> enroll (not a self-introduction). No enrollment log should appear.

### C3. Live recognition of the enrolled speaker
Speak normally for ~10s.
**PASS:** repeated `🗣️ Speaker identified: speaker_001 (score=…, reason=matched)`
and `/speaker_id: "speaker_001"`. Occasional `below_threshold` is acceptable as
long as the **published** id stays `speaker_001` (smoother holding).

### C4. Temporal smoothing
- One noisy/short segment misses → published id must **stay** `speaker_001`
  (look for `below_threshold … → /speaker_id: "speaker_001"`).
- Stop talking / several consecutive misses → after `smoothing_unknown_hits`
  (default 3) the published id releases to `"Unknown"`.
**PASS:** single misses do not flip the published id; sustained silence does.

### C5. Persistence across restart (THE portability feature)
Ctrl-C the launch, confirm there is **no `.wav`** in `voices/enrollment/`
(only `speaker_embeddings.json` + profiles), relaunch.
**PASS:** `✅ Speaker database loaded: 1 voices (speaker_001)` at startup, and
C3 recognition works again — with zero audio files present.

### C6. Portability to a "fresh robot"
```bash
mkdir -p /tmp/robotB/enrollment
cp voices/enrollment/speaker_embeddings.json voices/enrollment/*.profile.json /tmp/robotB/enrollment/
ros2 launch conversational_server full_system.launch.py config:=laptop \
  conversation_backend:=gemini_live
# (point the node at /tmp/robotB/enrollment via enrollment_dir param/launch arg)
```
**PASS:** the "new" instance loads `speaker_001` from the copied store alone and
recognizes the same speaker. No wav was ever transferred.

### C7. Multi-speaker discrimination (fixes the margin=-1 gap)
Enroll a 2nd person (repeat C2 with a different voice + name, e.g. "My name is
Delia"). Then have each person speak.
**PASS:**
- DB shows ≥2 voices at startup.
- Correct speaker identified for each; `margin` is now a real positive number,
  not `-1.000`.
- Cross-talk / a third unknown voice → `Unknown` (margin/threshold reject),
  no longer auto-assigned to speaker_001.

### C8. Switch hysteresis
With ≥2 enrolled, alternate speakers mid-conversation.
**PASS:** switching A→B requires `smoothing_switch_hits` (default 2) consecutive
B segments before `/speaker_id` flips; no single-segment ping-pong.

### C9. Name recall end-to-end
As a recognized speaker, ask: "What is my name?"
**PASS:** assistant answers "Vasile" (depends on `/person_context` injection;
requires clean transcription — see Known Issues).

---

## D. Robustness / guard rails

### D1. Model-version guard
Edit `speaker_embeddings.json` `"model"` to a bogus value, keep no wavs, relaunch.
**PASS:** `⚠️ Saved voiceprints were built with a different model …`, the stale
vector is ignored; if a wav exists it rebuilds, else that speaker is dropped
(not silently mismatched).

### D2. Corrupt store
Truncate `speaker_embeddings.json` to invalid JSON, ensure a `.wav` exists,
relaunch.
**PASS:** store load fails gracefully (no crash), DB rebuilt from `.wav`, store
re-persisted valid.

---

## Known issues to keep out of scope while testing the above
- Only 1 speaker enrolled ⇒ `margin=-1.000`; **enroll ≥2** before judging accuracy.
- Single-clip templates score low (0.3–0.4); enroll multiple clips for stable scores.
- Gemini STT may garble EN→Devanagari (`unsupported_script_turn`) and break
  intro/name detection — transcription quality, not speaker recognition.
- `tts_node` SIGSEGV (-11) on shutdown is unrelated to this implementation.

## Pass/Fail summary gate
The implementation passes if: A1+A2 green, C2 enrolls+persists, **C5 recognizes
with no wav present**, C6 recognizes on a copied store, C7 shows real margins
with ≥2 speakers, and D1 invalidates on model change.

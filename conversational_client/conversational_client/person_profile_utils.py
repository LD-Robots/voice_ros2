import json
import os
import re
import unicodedata

from .conversation_config import load_conversation_rules


_RULES = load_conversation_rules()
NAME_STOPWORDS = set(_RULES['name_stopwords'])
NAME_PREFIX_STOPWORDS = set(_RULES.get('name_prefix_stopwords') or ())
NAME_DISALLOWED_WORDS = set(_RULES.get('name_disallowed_words') or ())
NAME_SUFFIX_STOPWORDS = set(_RULES['name_suffix_stopwords'])
SELF_INTRO_NAME_PATTERNS = tuple(_RULES.get('self_intro_name_patterns') or _RULES['explicit_name_patterns'])
NAME_CORRECTION_PATTERNS = tuple(_RULES.get('name_correction_patterns') or ())
EXPLICIT_NAME_PATTERNS = SELF_INTRO_NAME_PATTERNS + NAME_CORRECTION_PATTERNS
PROFILE_SIDECAR_SUFFIX = '.profile.json'
NON_NAMING_GOVERNORS = tuple(_RULES.get('non_naming_governors') or ())
AFFIRMATION_WORDS = frozenset(_RULES.get('affirmation_words') or ())
NEGATION_WORDS = frozenset(_RULES.get('negation_words') or ())
EXPLICIT_INTRO_FRAMES = tuple(_RULES.get('explicit_intro_frames') or ())
COPULA_INTRO_FRAMES = tuple(_RULES.get('copula_intro_frames') or ())
LANGUAGE_PREFERENCES = {
    str(language): tuple(phrases)
    for language, phrases in (_RULES.get('language_preferences') or {}).items()
}

# Outcomes of classify_name_introduction.
INTRO_EXPLICIT = 'explicit'   # unambiguous naming frame -> safe to enrol
INTRO_COPULA = 'copula'       # "sunt X" -> could be a name, could be a job
INTRO_NONE = 'none'           # not an introduction at all -> never enrol

def normalize_person_name(raw_name: str) -> str:
    normalized = unicodedata.normalize('NFKD', raw_name or '')
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    words = re.findall(r'[a-z]{2,20}', normalized.lower())
    while words and words[-1] in NAME_SUFFIX_STOPWORDS:
        words.pop()
    if not words or len(words) > 2:
        return ''
    if words[0] in NAME_PREFIX_STOPWORDS:
        return ''
    if any(word in NAME_DISALLOWED_WORDS for word in words):
        return ''
    if any(len(word) < 2 or word in NAME_STOPWORDS for word in words):
        return ''
    return ' '.join(word.capitalize() for word in words)


def extract_preferred_name(normalized: str) -> str:
    return _extract_name_from_patterns(normalized, EXPLICIT_NAME_PATTERNS, allow_correction_cues=True)


def extract_auto_enrollment_name(normalized: str) -> str:
    return _extract_name_from_patterns(
        normalized,
        SELF_INTRO_NAME_PATTERNS,
        allow_correction_cues=False,
    )


def _extract_name_from_patterns(
    normalized: str,
    patterns,
    *,
    allow_correction_cues: bool,
) -> str:
    # Keep name extraction scoped to explicit introduction/correction cues so
    # ordinary conversation does not create bogus speaker profiles.
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        candidate = normalize_person_name(match.group(1))
        if candidate:
            return candidate
    spelled = _extract_spelled_name(normalized, allow_correction_cues=allow_correction_cues)
    if spelled:
        return spelled
    return ''


def _extract_spelled_name(normalized: str, *, allow_correction_cues: bool) -> str:
    text = (normalized or '').strip()
    if not text:
        return ''

    full_match = re.fullmatch(r'[a-z](?:\s+[a-z]){2,7}', text)
    if full_match:
        candidate = normalize_person_name(''.join(re.findall(r'[a-z]', full_match.group(0))))
        if candidate:
            return candidate

    cue_patterns = [
        r'\b(?:name is|ma numesc|ma cheama)\s+([a-z](?:\s+[a-z]){2,7})\b',
    ]
    if allow_correction_cues:
        cue_patterns.insert(0, r'\b(?:it s|it is|este|e)\s+([a-z](?:\s+[a-z]){2,7})\b')
    for pattern in cue_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = normalize_person_name(''.join(re.findall(r'[a-z]', match.group(1))))
        if candidate:
            return candidate
    return ''


def normalize_utterance(raw: str) -> str:
    """Fold an utterance to the same shape the name patterns are written in."""
    text = unicodedata.normalize('NFKD', raw or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9\s]', ' ', text.lower())
    return ' '.join(text.split())


def classify_name_introduction(name: str, utterance: str) -> tuple[str, str]:
    """Decide whether ``utterance`` really introduces ``name`` as a person's name.

    This audits the *grammatical role* of the name rather than checking it
    against a list of forbidden words. A blacklist cannot work here: person
    names and place names are both open sets and they overlap (Paris, Milan,
    Brooklyn are all both). But "I'm from Romania" and "my name is Vasile"
    differ structurally, and that difference generalises to every place,
    employer and topic without naming any of them.

    Returns ``(outcome, reason)`` where outcome is INTRO_EXPLICIT,
    INTRO_COPULA or INTRO_NONE.
    """
    candidate = normalize_utterance(name)
    text = normalize_utterance(utterance)
    if not candidate:
        return INTRO_NONE, 'empty name'
    if not text:
        # No evidence to audit. Callers that have no utterance to offer should
        # not get a free pass, since that is exactly the unchecked path that
        # let "Romania" through.
        return INTRO_NONE, 'no utterance to verify the name against'

    match = re.search(rf'\b{re.escape(candidate)}\b', text)
    if not match:
        return INTRO_NONE, f'name "{candidate}" does not appear in the utterance'

    before = text[:match.start()].split()
    tail = ' '.join(before)

    # Checked first, and allowed to win over the governor rule below: some
    # naming frames legitimately end in a preposition ("I go by Ana"), and an
    # explicit frame is unambiguous evidence in a way a bare preposition is not.
    # The frame must sit immediately before the name, so that
    # "my name is Vasile, I'm from Romania" validates Vasile and not Romania.
    for frame in EXPLICIT_INTRO_FRAMES:
        if tail.endswith(frame):
            return INTRO_EXPLICIT, f'explicit naming frame "{frame}"'

    # Otherwise the token immediately before the name decides its role.
    # "from Romania", "din Cluj", "at Google", "about Romania" are origins,
    # affiliations and topics -- never the speaker's name.
    if before and before[-1] in NON_NAMING_GOVERNORS:
        return INTRO_NONE, f'"{before[-1]} {candidate}" is a place/topic, not a name'

    for frame in COPULA_INTRO_FRAMES:
        if tail.endswith(frame):
            return INTRO_COPULA, f'ambiguous copula frame "{frame}"'

    return INTRO_NONE, 'no introduction frame precedes the name'


def interpret_confirmation_reply(normalized: str) -> str:
    """Read a yes/no answer to "is this your name?".

    A fallback for when the model does not report the answer itself. Returns
    'yes', 'no' or '' when the reply settles nothing. Negation is checked first
    so that "no, that's not right" is not read as agreement because of "right".
    """
    text = normalize_utterance(normalized)
    if not text:
        return ''
    words = set(text.split())
    if words & NEGATION_WORDS or any(p in text for p in NEGATION_WORDS if ' ' in p):
        return 'no'
    if words & AFFIRMATION_WORDS or any(p in text for p in AFFIRMATION_WORDS if ' ' in p):
        return 'yes'
    return ''


def resolve_preferred_name_update(
    existing_preferred_name: str,
    introduced_preferred_name: str,
) -> tuple[str, str, str]:
    existing = normalize_person_name(existing_preferred_name)
    introduced = normalize_person_name(introduced_preferred_name)
    if not introduced:
        return 'ignore', existing, introduced
    if not existing:
        return 'set', existing, introduced
    if existing == introduced:
        return 'keep', existing, introduced
    return 'conflict', existing, introduced


def extract_language_preference(normalized: str) -> str:
    for language in ('en', 'ro'):
        if any(token in normalized for token in LANGUAGE_PREFERENCES.get(language, ())):
            return language
    return ''


def extract_fact(normalized: str) -> str:
    patterns = (
        r'\bremember that (.+)\b',
        r'\btine minte ca (.+)\b',
        r'\bmy favorite ([a-z0-9 ]{2,20}) is ([a-z0-9 ]{2,40})\b',
        r'\bimi place ([a-z0-9 ]{2,40})\b',
        r'\bi like ([a-z0-9 ]{2,40})\b',
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        if len(match.groups()) == 2:
            return f'favorite {match.group(1).strip()} = {match.group(2).strip()}'
        return match.group(1).strip()
    return ''


def slugify_voice_name(preferred_name: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', preferred_name or '')
    ascii_name = ''.join(ch for ch in ascii_name if not unicodedata.combining(ch))
    ascii_name = ascii_name.lower()
    ascii_name = re.sub(r'[^a-z0-9]+', '_', ascii_name).strip('_')
    return ascii_name or 'person'


def build_unique_voice_label(preferred_name: str, existing_labels) -> str:
    existing = set(existing_labels or [])
    base = f'person_{slugify_voice_name(preferred_name)}'
    if base not in existing:
        return base

    suffix = 2
    while True:
        candidate = f'{base}_{suffix}'
        if candidate not in existing:
            return candidate
        suffix += 1


def build_unique_speaker_label(existing_labels) -> str:
    existing = {
        os.path.splitext((label or '').strip())[0]
        for label in (existing_labels or [])
        if str(label or '').strip()
    }
    suffix = 1
    while True:
        candidate = f'speaker_{suffix:03d}'
        if candidate not in existing:
            return candidate
        suffix += 1


def preferred_name_from_voice_label(voice_label: str) -> str:
    label = os.path.splitext((voice_label or '').strip())[0]
    if label.startswith('person_'):
        label = label[len('person_'):]
    words = [part for part in label.split('_') if part]
    if not words:
        return ''
    return ' '.join(word.capitalize() for word in words)


def default_preferred_name_for_voice_label(voice_label: str) -> str:
    label = os.path.splitext((voice_label or '').strip())[0]
    if not label or label.startswith('speaker_'):
        return ''
    return normalize_person_name(preferred_name_from_voice_label(label))


def normalize_person_record(voice_label: str, record: dict | None) -> dict:
    source = dict(record or {})
    preferred_name = normalize_person_name(str(source.get('preferred_name', '') or ''))
    if not preferred_name:
        preferred_name = default_preferred_name_for_voice_label(voice_label)

    preferred_language = str(source.get('preferred_language', '') or '').strip().lower()
    if preferred_language.startswith('en'):
        preferred_language = 'en'
    elif preferred_language.startswith('ro'):
        preferred_language = 'ro'
    else:
        preferred_language = ''

    facts = []
    for fact in list(source.get('facts', []) or []):
        text = str(fact or '').strip()
        if text and text not in facts:
            facts.append(text)

    return {
        'voice_label': voice_label,
        'preferred_name': preferred_name,
        'preferred_language': preferred_language,
        'facts': facts,
        'last_seen': str(source.get('last_seen', '') or ''),
    }


def speaker_profile_sidecar_path(enrollment_dir: str, voice_label: str) -> str:
    label = os.path.splitext((voice_label or '').strip())[0]
    return os.path.join(enrollment_dir, f'{label}{PROFILE_SIDECAR_SUFFIX}')


def load_speaker_profile_sidecars(enrollment_dir: str) -> dict:
    profiles = {}
    if not os.path.isdir(enrollment_dir):
        return profiles

    for name in os.listdir(enrollment_dir):
        if not name.endswith(PROFILE_SIDECAR_SUFFIX):
            continue
        voice_label = name[:-len(PROFILE_SIDECAR_SUFFIX)]
        path = os.path.join(enrollment_dir, name)
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except Exception:
            continue
        profiles[voice_label] = normalize_person_record(voice_label, payload)
    return profiles


def write_speaker_profile_sidecar(enrollment_dir: str, voice_label: str, record: dict | None):
    if not enrollment_dir:
        return
    os.makedirs(enrollment_dir, exist_ok=True)
    path = speaker_profile_sidecar_path(enrollment_dir, voice_label)
    normalized = normalize_person_record(voice_label, record)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(normalized, handle, indent=2, ensure_ascii=False)


def _merge_person_record_sources(voice_label: str, *records) -> dict:
    merged = normalize_person_record(voice_label, {})
    for record in records:
        normalized = normalize_person_record(voice_label, record)
        if normalized.get('preferred_name'):
            merged['preferred_name'] = normalized['preferred_name']
        if normalized.get('preferred_language'):
            merged['preferred_language'] = normalized['preferred_language']
        if normalized.get('last_seen'):
            merged['last_seen'] = normalized['last_seen']
        merged_facts = list(merged.get('facts', []))
        for fact in normalized.get('facts', []):
            if fact not in merged_facts:
                merged_facts.append(fact)
        merged['facts'] = merged_facts
    return normalize_person_record(voice_label, merged)


def migrate_legacy_auto_voice_labels(memory: dict | None, enrollment_dir: str) -> tuple[dict, bool, dict]:
    source_memory = memory if isinstance(memory, dict) else {}
    source_people = source_memory.get('people', {})
    people = source_people if isinstance(source_people, dict) else {}
    sidecar_people = load_speaker_profile_sidecars(enrollment_dir)

    wav_labels = set()
    if os.path.isdir(enrollment_dir):
        wav_labels = {
            os.path.splitext(name)[0]
            for name in os.listdir(enrollment_dir)
            if name.endswith('.wav')
        }

    legacy_labels = sorted(
        label for label in (set(people.keys()) | wav_labels | set(sidecar_people.keys()))
        if label.startswith('person_')
    )

    changed = False
    mapping = {}
    used_labels = set(people.keys()) | set(wav_labels) | set(sidecar_people.keys())
    for legacy_label in legacy_labels:
        new_label = build_unique_speaker_label(used_labels)
        used_labels.discard(legacy_label)
        used_labels.add(new_label)
        mapping[legacy_label] = new_label
        changed = True

    for legacy_label, new_label in mapping.items():
        old_wav = os.path.join(enrollment_dir, f'{legacy_label}.wav')
        new_wav = os.path.join(enrollment_dir, f'{new_label}.wav')
        if os.path.exists(old_wav) and old_wav != new_wav:
            try:
                os.replace(old_wav, new_wav)
            except FileNotFoundError:
                pass
        old_sidecar = speaker_profile_sidecar_path(enrollment_dir, legacy_label)
        new_sidecar = speaker_profile_sidecar_path(enrollment_dir, new_label)
        if os.path.exists(old_sidecar) and old_sidecar != new_sidecar:
            try:
                os.replace(old_sidecar, new_sidecar)
            except FileNotFoundError:
                pass

    normalized_people = {}
    for voice_label in sorted(set(people.keys()) | wav_labels | set(sidecar_people.keys())):
        target_label = mapping.get(voice_label, voice_label)
        normalized = _merge_person_record_sources(
            target_label,
            people.get(voice_label),
            sidecar_people.get(voice_label),
        )
        existing = normalized_people.get(target_label)
        if existing:
            merged_facts = list(existing.get('facts', []))
            for fact in normalized.get('facts', []):
                if fact not in merged_facts:
                    merged_facts.append(fact)
            if normalized.get('preferred_name'):
                existing['preferred_name'] = normalized['preferred_name']
            if normalized.get('preferred_language'):
                existing['preferred_language'] = normalized['preferred_language']
            if normalized.get('last_seen'):
                existing['last_seen'] = normalized['last_seen']
            existing['facts'] = merged_facts
            continue

        normalized_people[target_label] = normalized

        original = _merge_person_record_sources(
            voice_label,
            people.get(voice_label),
            sidecar_people.get(voice_label),
        )
        if normalized != original or target_label != voice_label:
            changed = True

    normalized_memory = {'people': normalized_people}
    if normalized_memory != source_memory:
        changed = True
    return normalized_memory, changed, mapping

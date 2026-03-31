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
LANGUAGE_PREFERENCES = {
    str(language): tuple(phrases)
    for language, phrases in (_RULES.get('language_preferences') or {}).items()
}

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

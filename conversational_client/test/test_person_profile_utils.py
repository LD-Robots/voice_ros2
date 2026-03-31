import json

from conversational_client.person_profile_utils import (
    extract_auto_enrollment_name,
    build_unique_speaker_label,
    build_unique_voice_label,
    default_preferred_name_for_voice_label,
    extract_language_preference,
    extract_preferred_name,
    load_speaker_profile_sidecars,
    migrate_legacy_auto_voice_labels,
    resolve_preferred_name_update,
    write_speaker_profile_sidecar,
)

def test_extract_preferred_name_requires_explicit_introduction():
    assert extract_preferred_name('my name is alex') == 'Alex'
    assert extract_preferred_name('call me maria') == 'Maria'
    assert extract_preferred_name('ma numesc andrei') == 'Andrei'
    assert extract_preferred_name('my name is vasile could you help me') == 'Vasile'
    assert extract_preferred_name('my name is ana nice to meet you') == 'Ana'
    assert extract_preferred_name('my name is vasile not vasily') == 'Vasile'
    assert extract_preferred_name('no it s oana') == 'Oana'
    assert extract_preferred_name('nu este oana') == 'Oana'
    assert extract_preferred_name('o a n a') == 'Oana'
    assert extract_preferred_name('it is o a n a') == 'Oana'
    assert extract_preferred_name('no it is fine') == ''
    assert extract_preferred_name('no it is the same') == ''
    assert extract_preferred_name('my name is the same') == ''
    assert extract_preferred_name('call me the robot') == ''
    assert extract_preferred_name('hello i am alex') == ''
    assert extract_preferred_name('i am back') == ''
    assert extract_preferred_name('i am working today') == ''
    assert extract_preferred_name('this is important for today') == ''


def test_extract_auto_enrollment_name_requires_self_introduction():
    assert extract_auto_enrollment_name('my name is alex') == 'Alex'
    assert extract_auto_enrollment_name('ma numesc andrei') == 'Andrei'
    assert extract_auto_enrollment_name('o a n a') == 'Oana'
    assert extract_auto_enrollment_name('no it s oana') == ''
    assert extract_auto_enrollment_name('actually it is oana') == ''
    assert extract_auto_enrollment_name('it is o a n a') == ''
    assert extract_auto_enrollment_name('my name is the same') == ''


def test_resolve_preferred_name_update_protects_existing_profiles():
    assert resolve_preferred_name_update('', 'alex') == ('set', '', 'Alex')
    assert resolve_preferred_name_update('Alex', 'alex') == ('keep', 'Alex', 'Alex')
    assert resolve_preferred_name_update('Vasile', 'Alex') == ('conflict', 'Vasile', 'Alex')


def test_extract_language_preference_supports_multiple_variants():
    assert extract_language_preference('answer in english please') == 'en'
    assert extract_language_preference('limba mea preferata este romana') == 'ro'


def test_build_unique_voice_label_avoids_collisions():
    assert build_unique_voice_label('Ana', {'person_ana'}) == 'person_ana_2'


def test_build_unique_speaker_label_uses_neutral_ids():
    assert build_unique_speaker_label({'speaker_001', 'delia'}) == 'speaker_002'


def test_default_preferred_name_for_voice_label_handles_manual_and_legacy_labels():
    assert default_preferred_name_for_voice_label('delia') == 'Delia'
    assert default_preferred_name_for_voice_label('person_vasile_could') == 'Vasile'
    assert default_preferred_name_for_voice_label('speaker_001') == ''


def test_migrate_legacy_auto_voice_labels_renames_person_labels(tmp_path):
    enrollment_dir = tmp_path / 'enrollment'
    enrollment_dir.mkdir()
    legacy_wav = enrollment_dir / 'person_vasile_could.wav'
    legacy_wav.write_bytes(b'RIFF')

    memory = {
        'people': {
            'person_vasile_could': {
                'voice_label': 'person_vasile_could',
                'preferred_name': 'Vasile Could',
                'preferred_language': 'english',
                'facts': ['likes robotics'],
                'last_seen': '2026-03-03T12:00:00+00:00',
            }
        }
    }

    migrated, changed, mapping = migrate_legacy_auto_voice_labels(memory, str(enrollment_dir))

    assert changed is True
    assert mapping == {'person_vasile_could': 'speaker_001'}
    assert json.loads(json.dumps(migrated)) == {
        'people': {
            'speaker_001': {
                'voice_label': 'speaker_001',
                'preferred_name': 'Vasile',
                'preferred_language': 'en',
                'facts': ['likes robotics'],
                'last_seen': '2026-03-03T12:00:00+00:00',
            }
        }
    }
    assert not legacy_wav.exists()
    assert (enrollment_dir / 'speaker_001.wav').exists()


def test_profile_sidecar_restores_missing_speaker_memory(tmp_path):
    enrollment_dir = tmp_path / 'enrollment'
    enrollment_dir.mkdir()
    (enrollment_dir / 'speaker_001.wav').write_bytes(b'RIFF')
    write_speaker_profile_sidecar(
        str(enrollment_dir),
        'speaker_001',
        {
            'preferred_name': 'Vasile',
            'preferred_language': 'ro',
            'facts': ['likes robotics'],
        },
    )

    migrated, changed, mapping = migrate_legacy_auto_voice_labels(
        {'people': {}},
        str(enrollment_dir),
    )

    assert changed is True
    assert mapping == {}
    assert migrated['people']['speaker_001']['preferred_name'] == 'Vasile'
    assert migrated['people']['speaker_001']['preferred_language'] == 'ro'
    assert migrated['people']['speaker_001']['facts'] == ['likes robotics']
    assert load_speaker_profile_sidecars(str(enrollment_dir))['speaker_001']['preferred_name'] == 'Vasile'

import json
from functools import lru_cache
from pathlib import Path


def _config_candidates() -> list[Path]:
    current = Path(__file__).resolve()
    candidates = [
        current.parents[1] / 'config' / 'conversation_rules.json',
    ]
    try:
        from ament_index_python.packages import get_package_share_directory
        candidates.append(
            Path(get_package_share_directory('conversational_client')) / 'config' / 'conversation_rules.json'
        )
    except Exception:
        pass
    return candidates


@lru_cache(maxsize=1)
def load_conversation_rules() -> dict:
    for path in _config_candidates():
        if path.exists():
            with open(path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
    raise FileNotFoundError('conversation_rules.json not found in source or installed config paths')

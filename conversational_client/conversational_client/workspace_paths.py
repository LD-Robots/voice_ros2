"""Locate the workspace root without depending on what the checkout is called.

The nodes need to reach runtime data that lives next to the sources (``voices/``,
``.env``, debug recordings). Every module used to find it by walking up until a
directory happened to be named ``voice_ros2``, which silently breaks the moment
the repository is cloned under any other name (``voice_ros2-main``, ``ws``, a
fork, a CI checkout...). Identify the workspace by what it *contains* instead,
and let deployments override it explicitly.

Resolution order:

1. ``$VOICE_ROS2_WS`` when it points at an existing directory.
2. The nearest ancestor that holds the source packages (detected via their
   ``package.xml``), searched from this file and from the current directory.
3. The legacy ``voice_ros2`` directory-name match, so existing checkouts that
   are laid out that way keep working even when the sources are absent.
"""

import os
from pathlib import Path

WORKSPACE_ENV_VAR = 'VOICE_ROS2_WS'
LEGACY_WORKSPACE_DIR_NAME = 'voice_ros2'
# A source workspace root is the directory that holds the packages themselves.
# Matching on package.xml (rather than on the directory names) also keeps the
# colcon ``install/`` tree from matching: it has conversational_* directories,
# but their package.xml lives under share/<pkg>/ instead.
_PACKAGE_MARKERS = (
    Path('conversational_client') / 'package.xml',
    Path('conversational_server') / 'package.xml',
)


def _candidate_bases(start: str | os.PathLike | None) -> list[Path]:
    bases = []
    if start is not None:
        bases.append(Path(start).resolve())
    bases.append(Path(__file__).resolve())
    try:
        bases.append(Path.cwd().resolve())
    except OSError:
        pass
    return bases


def find_workspace_root(start: str | os.PathLike | None = None) -> Path | None:
    """Return the workspace root, or ``None`` when it cannot be determined."""
    override = os.environ.get(WORKSPACE_ENV_VAR, '').strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return candidate.resolve()

    bases = _candidate_bases(start)
    for base in bases:
        for parent in [base] + list(base.parents):
            if all((parent / marker).is_file() for marker in _PACKAGE_MARKERS):
                return parent

    for base in bases:
        for parent in [base] + list(base.parents):
            if parent.name == LEGACY_WORKSPACE_DIR_NAME:
                return parent
    return None


def workspace_path(*parts: str, start: str | os.PathLike | None = None) -> Path | None:
    """Join ``parts`` onto the workspace root, or ``None`` if it is unknown."""
    root = find_workspace_root(start)
    if root is None:
        return None
    return root.joinpath(*parts)

#!/usr/bin/env python3
"""ReSpeaker XVF3000 tuning helpers (USB control endpoint)."""

from __future__ import annotations

import re
import struct
from typing import Dict, Optional, Tuple, Union

try:
    import usb.core
    import usb.util

    USB_AVAILABLE = True
except ImportError:
    USB_AVAILABLE = False

Number = Union[int, float]

# Based on the official ReSpeaker usb_4_mic_array tuning.py for XVF3000.
# tuple format: (unit_id, offset, value_type, max, min, mode)
PARAMETERS: Dict[str, Tuple[int, int, str, Number, Number, str]] = {
    'AECFREEZEONOFF': (18, 7, 'int', 1, 0, 'rw'),
    'AECNORM': (18, 19, 'float', 16.0, 0.25, 'rw'),
    'AECPATHCHANGE': (18, 25, 'int', 1, 0, 'ro'),
    'RT60': (18, 26, 'float', 0.9, 0.25, 'ro'),
    'HPFONOFF': (18, 27, 'int', 3, 0, 'rw'),
    'RT60ONOFF': (18, 28, 'int', 1, 0, 'rw'),
    'AECSILENCELEVEL': (18, 30, 'float', 1.0, 1e-9, 'rw'),
    'AECSILENCEMODE': (18, 31, 'int', 1, 0, 'ro'),
    'AGCONOFF': (19, 0, 'int', 1, 0, 'rw'),
    'AGCMAXGAIN': (19, 1, 'float', 1000.0, 1.0, 'rw'),
    'AGCDESIREDLEVEL': (19, 2, 'float', 0.99, 1e-8, 'rw'),
    'AGCGAIN': (19, 3, 'float', 1000.0, 1.0, 'rw'),
    'AGCTIME': (19, 4, 'float', 1.0, 0.1, 'rw'),
    'CNIONOFF': (19, 5, 'int', 1, 0, 'rw'),
    'FREEZEONOFF': (19, 6, 'int', 1, 0, 'rw'),
    'STATNOISEONOFF': (19, 8, 'int', 1, 0, 'rw'),
    'GAMMA_NS': (19, 9, 'float', 3.0, 0.0, 'rw'),
    'MIN_NS': (19, 10, 'float', 1.0, 0.0, 'rw'),
    'NONSTATNOISEONOFF': (19, 11, 'int', 1, 0, 'rw'),
    'GAMMA_NN': (19, 12, 'float', 3.0, 0.0, 'rw'),
    'MIN_NN': (19, 13, 'float', 1.0, 0.0, 'rw'),
    'ECHOONOFF': (19, 14, 'int', 1, 0, 'rw'),
    'GAMMA_E': (19, 15, 'float', 3.0, 0.0, 'rw'),
    'GAMMA_ETAIL': (19, 16, 'float', 3.0, 0.0, 'rw'),
    'GAMMA_ENL': (19, 17, 'float', 5.0, 0.0, 'rw'),
    'NLATTENONOFF': (19, 18, 'int', 1, 0, 'rw'),
    'NLAEC_MODE': (19, 20, 'int', 2, 0, 'rw'),
    'SPEECHDETECTED': (19, 22, 'int', 1, 0, 'ro'),
    'FSBUPDATED': (19, 23, 'int', 1, 0, 'ro'),
    'FSBPATHCHANGE': (19, 24, 'int', 1, 0, 'ro'),
    'TRANSIENTONOFF': (19, 29, 'int', 1, 0, 'rw'),
    'VOICEACTIVITY': (19, 32, 'int', 1, 0, 'ro'),
    'STATNOISEONOFF_SR': (19, 33, 'int', 1, 0, 'rw'),
    'NONSTATNOISEONOFF_SR': (19, 34, 'int', 1, 0, 'rw'),
    'GAMMA_NS_SR': (19, 35, 'float', 3.0, 0.0, 'rw'),
    'GAMMA_NN_SR': (19, 36, 'float', 3.0, 0.0, 'rw'),
    'MIN_NS_SR': (19, 37, 'float', 1.0, 0.0, 'rw'),
    'MIN_NN_SR': (19, 38, 'float', 1.0, 0.0, 'rw'),
    'GAMMAVAD_SR': (19, 39, 'float', 1000.0, 0.0, 'rw'),
    'DOAANGLE': (21, 0, 'int', 359, 0, 'ro'),
}

# Profiles tuned for conversational robots with playback enabled.
# NOTE: AGCONOFF -> 0 means AGC OFF, 1 means AGC ON.
TUNING_PROFILES: Dict[str, Dict[str, Number]] = {
    'none': {},
    'voice_assistant': {
        'AECFREEZEONOFF': 0,
        'ECHOONOFF': 1,
        'NLATTENONOFF': 1,
        'TRANSIENTONOFF': 1,
        'STATNOISEONOFF_SR': 1,
        'NONSTATNOISEONOFF_SR': 1,
        'GAMMAVAD_SR': 2.8,
        'AGCONOFF': 1,
    },
    'balanced_listen': {
        'AECFREEZEONOFF': 0,
        'FREEZEONOFF': 0,
        'ECHOONOFF': 1,
        'NLATTENONOFF': 1,
        'TRANSIENTONOFF': 1,
        'STATNOISEONOFF_SR': 1,
        'NONSTATNOISEONOFF_SR': 1,
        'GAMMA_E': 1.4,
        'GAMMA_ETAIL': 1.5,
        'GAMMA_ENL': 1.0,
        'GAMMAVAD_SR': 2.4,
        'AGCONOFF': 1,
        'AGCMAXGAIN': 12.0,
        'AGCDESIREDLEVEL': 0.25,
        'AGCTIME': 0.3,
    },
    'aggressive_echo_guard': {
        'AECFREEZEONOFF': 0,
        'FREEZEONOFF': 0,
        'ECHOONOFF': 1,
        'NLATTENONOFF': 1,
        'TRANSIENTONOFF': 1,
        'STATNOISEONOFF_SR': 1,
        'NONSTATNOISEONOFF_SR': 1,
        'GAMMA_E': 1.6,
        'GAMMA_ETAIL': 1.8,
        'GAMMA_ENL': 1.2,
        'GAMMAVAD_SR': 3.2,
        'AGCONOFF': 0,
    },
}


class ReSpeakerTuning:
    """USB control wrapper for ReSpeaker XVF3000 tuning."""

    TIMEOUT_MS = 100000

    def __init__(self, dev):
        self.dev = dev

    @classmethod
    def find(
        cls,
        vid: int = 0x2886,
        pid: int = 0x0018,
    ) -> Optional['ReSpeakerTuning']:
        if not USB_AVAILABLE:
            return None
        dev = usb.core.find(idVendor=int(vid), idProduct=int(pid))
        if dev is None:
            return None
        return cls(dev)

    def close(self):
        usb.util.dispose_resources(self.dev)

    def read(self, name: str) -> Number:
        key = name.upper().strip()
        if key not in PARAMETERS:
            raise KeyError(f'Unknown ReSpeaker parameter: {name}')

        unit_id, offset, value_type, _, _, _ = PARAMETERS[key]
        cmd = 0x80 | int(offset)
        if value_type == 'int':
            cmd |= 0x40

        response = self.dev.ctrl_transfer(
            usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            cmd,
            unit_id,
            8,
            self.TIMEOUT_MS,
        )
        i0, i1 = struct.unpack('ii', bytes(response))
        if value_type == 'int':
            return int(i0)
        return float(i0) * (2.0 ** float(i1))

    def write(self, name: str, value: Number):
        key = name.upper().strip()
        if key not in PARAMETERS:
            raise KeyError(f'Unknown ReSpeaker parameter: {name}')

        unit_id, offset, value_type, max_v, min_v, mode = PARAMETERS[key]
        if mode == 'ro':
            raise ValueError(f'{key} is read-only')

        numeric_value: Number
        if value_type == 'int':
            numeric_value = int(float(value))
        else:
            numeric_value = float(value)

        if numeric_value < min_v or numeric_value > max_v:
            raise ValueError(f'{key}={numeric_value} outside allowed range [{min_v}, {max_v}]')

        if value_type == 'int':
            payload = struct.pack('iii', int(offset), int(numeric_value), 1)
        else:
            payload = struct.pack('ifi', int(offset), float(numeric_value), 0)

        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            0,
            unit_id,
            payload,
            self.TIMEOUT_MS,
        )

    def apply(self, mapping: Dict[str, Number]):
        for name, value in mapping.items():
            self.write(name, value)


def parse_tuning_overrides(raw: str) -> Dict[str, Number]:
    """Parse `NAME=VALUE` pairs separated by comma/semicolon/whitespace."""
    if not raw:
        return {}

    out: Dict[str, Number] = {}
    tokens = [tok.strip() for tok in re.split(r'[,;\\s]+', raw) if tok.strip()]
    for token in tokens:
        if '=' not in token:
            raise ValueError(
                f'Invalid override token "{token}". Expected NAME=VALUE,NAME=VALUE'
            )
        name, value = token.split('=', 1)
        key = name.upper().strip()
        if key not in PARAMETERS:
            raise ValueError(f'Unknown ReSpeaker override parameter: {key}')

        value_type = PARAMETERS[key][2]
        if value_type == 'int':
            out[key] = int(float(value.strip()))
        else:
            out[key] = float(value.strip())
    return out


def resolve_profile(profile_name: str) -> Dict[str, Number]:
    key = (profile_name or 'none').strip().lower()
    if key not in TUNING_PROFILES:
        available = ', '.join(sorted(TUNING_PROFILES.keys()))
        raise ValueError(f'Unknown ReSpeaker tuning profile: {profile_name}. Available: {available}')
    return dict(TUNING_PROFILES[key])

#!/usr/bin/env python3
"""
Person Memory Store Node.

Keeps a JSON-backed store of per-speaker names, preferences, and simple facts.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
from std_msgs.msg import String

from .conversation_utils import normalize_text


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


class PersonMemoryStoreNode(Node):
    def __init__(self):
        super().__init__('person_memory_store_node')

        workspace_root = _find_workspace_root()
        default_memory_path = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'person_memory.json',
        )

        self.declare_parameter('memory_file', default_memory_path)
        self.declare_parameter('max_facts_per_person', 20)

        self.memory_file = str(self.get_parameter('memory_file').value)
        self.max_facts_per_person = int(self.get_parameter('max_facts_per_person').value)

        self.current_speaker = 'Unknown'
        self.memory = self._load_memory()

        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/attended_transcription',
            self._transcription_callback,
            10,
        )
        self.context_pub = self.create_publisher(String, '/person_context', 10)

        self.get_logger().info(f'Person Memory Store started: {self.memory_file}')

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip() or 'Unknown'
        self.current_speaker = speaker
        if speaker == 'Unknown':
            self._publish_context()
            return
        self._touch_person(speaker)
        self._publish_context()

    def _transcription_callback(self, msg: Transcription):
        if self.current_speaker == 'Unknown':
            return

        normalized = normalize_text(msg.text)
        if not normalized:
            return

        record = self._touch_person(self.current_speaker)
        updated = False

        preferred_name = self._extract_preferred_name(normalized)
        if preferred_name and record.get('preferred_name') != preferred_name:
            record['preferred_name'] = preferred_name
            updated = True

        preferred_language = self._extract_language_preference(normalized)
        if preferred_language and record.get('preferred_language') != preferred_language:
            record['preferred_language'] = preferred_language
            updated = True

        fact = self._extract_fact(normalized)
        if fact:
            facts = list(record.get('facts', []))
            if fact not in facts:
                facts.append(fact)
                record['facts'] = facts[-self.max_facts_per_person:]
                updated = True

        if updated:
            self._save_memory()
            self.get_logger().info(f'Updated memory for speaker={self.current_speaker}')
            self._publish_context()

    def _touch_person(self, speaker: str):
        people = self.memory.setdefault('people', {})
        record = people.setdefault(speaker, {
            'voice_label': speaker,
            'preferred_name': '',
            'preferred_language': '',
            'facts': [],
            'last_seen': '',
        })
        record['last_seen'] = datetime.now(timezone.utc).isoformat()
        self._save_memory()
        return record

    def _publish_context(self):
        if self.current_speaker == 'Unknown':
            payload = {
                'speaker': 'Unknown',
                'preferred_name': '',
                'preferred_language': '',
                'facts': [],
            }
        else:
            record = self._touch_person(self.current_speaker)
            payload = {
                'speaker': self.current_speaker,
                'preferred_name': record.get('preferred_name', ''),
                'preferred_language': record.get('preferred_language', ''),
                'facts': record.get('facts', []),
            }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.context_pub.publish(msg)

    def _load_memory(self):
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {'people': {}}
        except Exception as exc:
            self.get_logger().warning(f'Failed to load person memory: {exc}')
            return {'people': {}}

    def _save_memory(self):
        memory_dir = os.path.dirname(self.memory_file)
        if memory_dir:
            os.makedirs(memory_dir, exist_ok=True)
        with open(self.memory_file, 'w', encoding='utf-8') as handle:
            json.dump(self.memory, handle, indent=2, ensure_ascii=False)

    @staticmethod
    def _extract_preferred_name(normalized: str) -> str:
        patterns = (
            r'\bmy name is ([a-z0-9]{2,30})\b',
            r'\bcall me ([a-z0-9]{2,30})\b',
            r'\bma numesc ([a-z0-9]{2,30})\b',
            r'\bspune mi ([a-z0-9]{2,30})\b',
            r'\bzi mi ([a-z0-9]{2,30})\b',
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return match.group(1).capitalize()
        return ''

    @staticmethod
    def _extract_language_preference(normalized: str) -> str:
        if any(token in normalized for token in ('speak english', 'talk in english', 'vorbeste in engleza')):
            return 'en'
        if any(token in normalized for token in ('speak romanian', 'talk in romanian', 'vorbeste in romana')):
            return 'ro'
        return ''

    @staticmethod
    def _extract_fact(normalized: str) -> str:
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


def main(args=None):
    rclpy.init(args=args)
    node = PersonMemoryStoreNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Robot Command Executor Node.

Consumes normalized voice commands and executes controller-side actions.
"""
import queue
import json
import re
import threading
import time
import unicodedata

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand, Transcription
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from std_srvs.srv import Trigger


class RobotCommandExecutorNode(Node):
    def __init__(self):
        super().__init__('robot_command_executor_node')

        self.declare_parameter('execution_enabled', True)
        self.declare_parameter('min_command_confidence', 0.60)
        self.declare_parameter('max_pending_commands', 20)

        self.declare_parameter('move_mode', 'twist')  # twist | topic
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('move_topic', '/robot_move_command')
        self.declare_parameter('step_length_m', 0.25)
        self.declare_parameter('linear_speed_mps', 0.15)
        self.declare_parameter('angular_speed_rps', 0.80)
        self.declare_parameter('control_rate_hz', 20.0)
        self.declare_parameter('max_move_duration_s', 20.0)
        self.declare_parameter('max_turn_duration_s', 12.0)

        self.declare_parameter('behavior_mode', 'topic')  # topic | service | both
        self.declare_parameter('behavior_topic', '/robot_behavior_command')
        self.declare_parameter('raise_hands_service', '/raise_hands')
        self.declare_parameter('lower_hands_service', '/lower_hands')
        self.declare_parameter('wave_service', '/wave')
        self.declare_parameter('dance_service', '/dance')
        self.declare_parameter('service_timeout_s', 3.0)

        # Command-state handling
        self.declare_parameter('preempt_on_new_command', True)
        self.declare_parameter('enable_voice_cancel', True)
        self.declare_parameter('cancel_words', 'stop,cancel,halt,opreste,anuleaza')
        self.declare_parameter('enable_risky_confirmation', True)
        self.declare_parameter('confirmation_timeout_s', 6.0)
        self.declare_parameter('confirmation_accept_words', 'yes,confirm,ok,da,confirma')
        self.declare_parameter('confirmation_reject_words', 'no,reject,nu,anuleaza')
        self.declare_parameter('require_same_speaker_for_confirmation', True)
        self.declare_parameter('risky_steps_threshold', 5)
        self.declare_parameter('risky_backward_steps_threshold', 3)
        self.declare_parameter('risky_turn_angle_deg', 150.0)
        self.declare_parameter('require_confirmation_for_dance', False)
        self.declare_parameter('require_confirmation_for_raise_hands', False)
        self.declare_parameter('transcription_topic', '/attended_transcription')

        self.execution_enabled = bool(self.get_parameter('execution_enabled').value)
        self.min_command_confidence = float(self.get_parameter('min_command_confidence').value)
        self.max_pending_commands = int(self.get_parameter('max_pending_commands').value)

        self.move_mode = str(self.get_parameter('move_mode').value)
        self.cmd_vel_topic = str(self.get_parameter('cmd_vel_topic').value)
        self.move_topic = str(self.get_parameter('move_topic').value)
        self.step_length_m = float(self.get_parameter('step_length_m').value)
        self.linear_speed_mps = float(self.get_parameter('linear_speed_mps').value)
        self.angular_speed_rps = float(self.get_parameter('angular_speed_rps').value)
        self.control_rate_hz = float(self.get_parameter('control_rate_hz').value)
        self.max_move_duration_s = float(self.get_parameter('max_move_duration_s').value)
        self.max_turn_duration_s = float(self.get_parameter('max_turn_duration_s').value)

        self.behavior_mode = str(self.get_parameter('behavior_mode').value)
        self.behavior_topic = str(self.get_parameter('behavior_topic').value)
        self.raise_hands_service = str(self.get_parameter('raise_hands_service').value)
        self.lower_hands_service = str(self.get_parameter('lower_hands_service').value)
        self.wave_service = str(self.get_parameter('wave_service').value)
        self.dance_service = str(self.get_parameter('dance_service').value)
        self.service_timeout_s = float(self.get_parameter('service_timeout_s').value)

        self.preempt_on_new_command = bool(self.get_parameter('preempt_on_new_command').value)
        self.enable_voice_cancel = bool(self.get_parameter('enable_voice_cancel').value)
        self.enable_risky_confirmation = bool(self.get_parameter('enable_risky_confirmation').value)
        self.confirmation_timeout_s = float(self.get_parameter('confirmation_timeout_s').value)
        self.require_same_speaker_for_confirmation = bool(
            self.get_parameter('require_same_speaker_for_confirmation').value
        )
        self.risky_steps_threshold = int(self.get_parameter('risky_steps_threshold').value)
        self.risky_backward_steps_threshold = int(self.get_parameter('risky_backward_steps_threshold').value)
        self.risky_turn_angle_deg = float(self.get_parameter('risky_turn_angle_deg').value)
        self.require_confirmation_for_dance = bool(
            self.get_parameter('require_confirmation_for_dance').value
        )
        self.require_confirmation_for_raise_hands = bool(
            self.get_parameter('require_confirmation_for_raise_hands').value
        )
        transcription_topic = str(self.get_parameter('transcription_topic').value)
        self.cancel_words = self._parse_words(self.get_parameter('cancel_words').value)
        self.confirm_accept_words = self._parse_words(self.get_parameter('confirmation_accept_words').value)
        self.confirm_reject_words = self._parse_words(self.get_parameter('confirmation_reject_words').value)

        self.current_speaker = 'Unknown'

        self.command_sub = self.create_subscription(
            RobotCommand,
            '/robot_command',
            self._command_callback,
            10
        )
        self.tts_cmd_pub = self.create_publisher(
            String,
            '/tts_command',
            10
        )
        self.transcription_sub = self.create_subscription(
            Transcription,
            transcription_topic,
            self._transcription_callback,
            10
        )
        self.speaker_sub = self.create_subscription(
            String,
            '/speaker_id',
            self._speaker_callback,
            10
        )

        self.cmd_vel_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10)
        self.move_topic_pub = self.create_publisher(String, self.move_topic, 10)
        self.behavior_pub = self.create_publisher(String, self.behavior_topic, 10)
        self.status_pub = self.create_publisher(String, '/robot_command_status', 10)

        self.raise_hands_client = self.create_client(Trigger, self.raise_hands_service)
        self.lower_hands_client = self.create_client(Trigger, self.lower_hands_service)
        self.wave_client = self.create_client(Trigger, self.wave_service)
        self.dance_client = self.create_client(Trigger, self.dance_service)
        self.behavior_clients = {
            'raise_hands': self.raise_hands_client,
            'lower_hands': self.lower_hands_client,
            'wave': self.wave_client,
            'dance': self.dance_client,
        }

        self._queue = queue.Queue(maxsize=self.max_pending_commands)
        self._cancel_event = threading.Event()
        self._pending_confirmation = None
        self._pending_confirmation_speaker = 'Unknown'
        self._pending_confirmation_deadline = 0.0
        self._pending_lock = threading.Lock()

        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name='robot-command-worker')
        self._worker.start()
        self._confirm_timer = self.create_timer(0.25, self._confirmation_timer_callback)

        self.get_logger().info(
            'Robot Command Executor started: '
            f'execution_enabled={self.execution_enabled}, move_mode={self.move_mode}, behavior_mode={self.behavior_mode}, '
            f'preempt_on_new_command={self.preempt_on_new_command}, risky_confirmation={self.enable_risky_confirmation}'
        )

    def destroy_node(self):
        self._running = False
        self._cancel_active_execution('shutdown')
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        return super().destroy_node()

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip()
        self.current_speaker = speaker if speaker else 'Unknown'

    def _command_callback(self, msg: RobotCommand):
        if not self.execution_enabled:
            return

        if msg.confidence < self.min_command_confidence:
            self.get_logger().debug(
                f'Ignoring command below threshold ({msg.confidence:.2f} < {self.min_command_confidence:.2f})'
            )
            return

        if self._requires_confirmation(msg):
            if self.preempt_on_new_command:
                self._cancel_active_execution('preempted by risky command awaiting confirmation')
                self._clear_queue()
            self._set_pending_confirmation(msg)
            return

        self._clear_pending_confirmation('replaced by a new command')
        self._enqueue_command(msg, preempt=self.preempt_on_new_command)

    def _transcription_callback(self, msg: Transcription):
        text = self._normalize_text(msg.text)
        if not text:
            return

        if self.enable_voice_cancel and self._contains_any(text, self.cancel_words):
            self._clear_pending_confirmation('canceled by voice')
            self._cancel_active_execution('voice cancel')
            self._clear_queue()
            self._publish_status('canceled')
            return

        pending = self._get_pending_confirmation()
        if pending is None:
            return

        pending_msg, pending_speaker, deadline = pending
        if time.monotonic() > deadline:
            self._clear_pending_confirmation('confirmation timeout')
            self._publish_status('confirmation_timeout')
            return

        if self.require_same_speaker_for_confirmation:
            current_speaker = self.current_speaker if self.current_speaker else 'Unknown'
            if pending_speaker != 'Unknown' and current_speaker != pending_speaker:
                self.get_logger().warn(
                    'Ignoring confirmation from a different speaker: '
                    f'expected={pending_speaker}, got={current_speaker}'
                )
                return

        if self._contains_any(text, self.confirm_accept_words):
            self._clear_pending_confirmation('confirmed')
            self._enqueue_command(pending_msg, preempt=True)
            self._publish_status('confirmation_accepted')
            return

        if self._contains_any(text, self.confirm_reject_words):
            self._clear_pending_confirmation('rejected')
            self._publish_status('confirmation_rejected')
            return

    def _worker_loop(self):
        while self._running:
            try:
                msg = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if msg is None:
                continue

            try:
                self._cancel_event.clear()
                self._execute(msg)
            except Exception as exc:
                self.get_logger().error(f'Command execution failed: {exc}')

    def _execute(self, msg: RobotCommand):
        intent = msg.intent.strip().lower()
        if intent == 'stop':
            self._cancel_active_execution('stop intent')
            self._clear_queue()
            self._publish_status('stopped')
            return
        if intent == 'move':
            self._execute_move(msg)
            return
        if intent == 'turn':
            self._execute_turn(msg)
            return
        if intent in ('raise_hands', 'lower_hands', 'wave', 'dance'):
            self._execute_behavior(intent)
            return
        self.get_logger().warn(f'Unsupported intent: {intent}')

    def _execute_move(self, msg: RobotCommand):
        direction = msg.direction.strip().lower()
        steps = max(1, int(msg.steps) if msg.steps > 0 else 1)

        sign = 0.0
        if direction == 'forward':
            sign = 1.0
        elif direction == 'backward':
            sign = -1.0
        else:
            self.get_logger().warn(f'Invalid move direction: {direction}')
            return

        if self.move_mode == 'topic':
            payload = String()
            payload.data = f'{direction}:{steps}'
            self.move_topic_pub.publish(payload)
            self.get_logger().info(f'Published move topic command: {payload.data}')
            self._publish_status(f'executed_move_topic:{payload.data}')
            return

        speed = abs(self.linear_speed_mps)
        if speed <= 0.0:
            self.get_logger().warn('linear_speed_mps must be > 0. Move ignored.')
            return

        distance = steps * max(0.01, self.step_length_m)
        duration = min(self.max_move_duration_s, distance / speed)
        if duration <= 0.0:
            self.get_logger().warn('Computed move duration is 0. Move ignored.')
            return

        rate_hz = max(1.0, self.control_rate_hz)
        period = 1.0 / rate_hz
        end_t = time.monotonic() + duration

        if self.cmd_vel_pub.get_subscription_count() == 0:
            self.get_logger().warn(f'No subscribers on {self.cmd_vel_topic}. Command may not move the robot.')

        cmd = Twist()
        cmd.linear.x = sign * speed

        while self._running and time.monotonic() < end_t and not self._cancel_event.is_set():
            self.cmd_vel_pub.publish(cmd)
            time.sleep(period)

        self._stop_motion()
        if self._cancel_event.is_set():
            self.get_logger().info(f'Move canceled: direction={direction}, steps={steps}')
            self._publish_status('move_canceled')
            return

        self.get_logger().info(f'Executed move: direction={direction}, steps={steps}, duration={duration:.2f}s')
        self._publish_status(f'executed_move:{direction}:{steps}')

    def _execute_turn(self, msg: RobotCommand):
        direction = msg.direction.strip().lower()
        if direction not in ('left', 'right'):
            self.get_logger().warn(f'Invalid turn direction: {direction}')
            return

        angle_deg = self._extract_angle_deg(msg.parameters_json)
        if self.move_mode == 'topic':
            payload = String()
            payload.data = f'turn:{direction}:{int(angle_deg)}'
            self.move_topic_pub.publish(payload)
            self.get_logger().info(f'Published turn topic command: {payload.data}')
            self._publish_status(f'executed_turn_topic:{direction}:{int(angle_deg)}')
            return

        angular_speed = abs(self.angular_speed_rps)
        if angular_speed <= 0.0:
            self.get_logger().warn('angular_speed_rps must be > 0. Turn ignored.')
            return

        angle_rad = angle_deg * 3.141592653589793 / 180.0
        duration = min(self.max_turn_duration_s, angle_rad / angular_speed)
        if duration <= 0.0:
            self.get_logger().warn('Computed turn duration is 0. Turn ignored.')
            return

        rate_hz = max(1.0, self.control_rate_hz)
        period = 1.0 / rate_hz
        end_t = time.monotonic() + duration

        if self.cmd_vel_pub.get_subscription_count() == 0:
            self.get_logger().warn(f'No subscribers on {self.cmd_vel_topic}. Command may not move the robot.')

        cmd = Twist()
        cmd.angular.z = angular_speed if direction == 'left' else -angular_speed

        while self._running and time.monotonic() < end_t and not self._cancel_event.is_set():
            self.cmd_vel_pub.publish(cmd)
            time.sleep(period)

        self._stop_motion()
        if self._cancel_event.is_set():
            self.get_logger().info(f'Turn canceled: direction={direction}, angle={angle_deg}')
            self._publish_status('turn_canceled')
            return

        self.get_logger().info(f'Executed turn: direction={direction}, angle={angle_deg}, duration={duration:.2f}s')
        self._publish_status(f'executed_turn:{direction}:{int(angle_deg)}')

    def _execute_behavior(self, intent: str):
        mode = self.behavior_mode.lower()

        if mode in ('topic', 'both'):
            msg = String()
            msg.data = intent
            self.behavior_pub.publish(msg)
            self.get_logger().info(f'Published behavior command: {intent}')
            self._publish_status(f'executed_behavior_topic:{intent}')

        if mode in ('service', 'both'):
            client = self.behavior_clients.get(intent)
            if client is None:
                self.get_logger().warn(f'No service client configured for behavior: {intent}')
                return
            if not client.wait_for_service(timeout_sec=0.5):
                self.get_logger().warn(f'Service unavailable for {intent}.')
                return

            req = Trigger.Request()
            future = client.call_async(req)
            end_t = time.monotonic() + max(0.1, self.service_timeout_s)
            while (
                self._running
                and not self._cancel_event.is_set()
                and (not future.done())
                and time.monotonic() < end_t
            ):
                time.sleep(0.05)

            if self._cancel_event.is_set():
                self.get_logger().info(f'Behavior canceled while waiting for service result: {intent}')
                self._publish_status('behavior_canceled')
                return

            if not future.done():
                self.get_logger().warn(f'Service call timeout for {intent}.')
                return

            result = future.result()
            if result is None:
                self.get_logger().warn(f'Service call returned no result for {intent}.')
                return

            if result.success:
                self.get_logger().info(f'Service execution succeeded for {intent}: {result.message}')
                self._publish_status(f'executed_behavior_service:{intent}:ok')
            else:
                self.get_logger().warn(f'Service execution failed for {intent}: {result.message}')
                self._publish_status(f'executed_behavior_service:{intent}:failed')

    def _enqueue_command(self, msg: RobotCommand, preempt: bool):
        if preempt:
            self._cancel_active_execution('preempted by new command')
            self._clear_queue()

        try:
            self._queue.put_nowait(msg)
            self._publish_status(f'queued:{msg.intent}')
        except queue.Full:
            self.get_logger().warn('Command queue is full. Dropping command.')

    def _cancel_active_execution(self, reason: str):
        self._cancel_event.set()
        self._stop_motion()
        try:
            if rclpy.ok():
                self.get_logger().info(f'Active execution canceled: {reason}')
            else:
                print(f'Active execution canceled: {reason}')
        except Exception:
            pass

    def _stop_motion(self):
        try:
            if rclpy.ok():
                self.cmd_vel_pub.publish(Twist())
        except Exception:
            pass

    def _clear_queue(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _requires_confirmation(self, msg: RobotCommand) -> bool:
        if not self.enable_risky_confirmation:
            return False

        intent = (msg.intent or '').strip().lower()
        if intent == 'dance' and self.require_confirmation_for_dance:
            return True
        if intent == 'raise_hands' and self.require_confirmation_for_raise_hands:
            return True
        if intent == 'turn':
            angle_deg = self._extract_angle_deg(msg.parameters_json)
            return angle_deg >= max(5.0, self.risky_turn_angle_deg)
        if intent != 'move':
            return False

        steps = int(msg.steps) if msg.steps > 0 else 1
        direction = (msg.direction or '').strip().lower()

        if steps >= max(1, self.risky_steps_threshold):
            return True
        if direction == 'backward' and steps >= max(1, self.risky_backward_steps_threshold):
            return True
        return False

    def _set_pending_confirmation(self, msg: RobotCommand):
        deadline = time.monotonic() + max(1.0, self.confirmation_timeout_s)
        speaker = msg.speaker if msg.speaker else 'Unknown'
        with self._pending_lock:
            self._pending_confirmation = msg
            self._pending_confirmation_speaker = speaker
            self._pending_confirmation_deadline = deadline
        self.get_logger().info(
            f'Confirmation required for risky command: intent={msg.intent}, '
            f'direction={msg.direction}, steps={msg.steps}, speaker={speaker}'
        )
        self._publish_status('confirmation_required')
        if (msg.language or '').lower().startswith('ro'):
            tts_msg = String()
            tts_msg.data = 'confirm_ro'
            self.tts_cmd_pub.publish(tts_msg)
        else:
            tts_msg = String()
            tts_msg.data = 'confirm_en'
            self.tts_cmd_pub.publish(tts_msg)

    def _get_pending_confirmation(self):
        with self._pending_lock:
            if self._pending_confirmation is None:
                return None
            return (
                self._pending_confirmation,
                self._pending_confirmation_speaker,
                self._pending_confirmation_deadline,
            )

    def _clear_pending_confirmation(self, reason: str):
        with self._pending_lock:
            had_pending = self._pending_confirmation is not None
            self._pending_confirmation = None
            self._pending_confirmation_speaker = 'Unknown'
            self._pending_confirmation_deadline = 0.0
        if had_pending:
            self.get_logger().info(f'Pending confirmation cleared: {reason}')

    def _confirmation_timer_callback(self):
        pending = self._get_pending_confirmation()
        if pending is None:
            return
        _, _, deadline = pending
        if time.monotonic() > deadline:
            self._clear_pending_confirmation('timeout')
            self._publish_status('confirmation_timeout')

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    @staticmethod
    def _extract_angle_deg(parameters_json: str) -> float:
        if not parameters_json:
            return 90.0
        try:
            payload = json.loads(parameters_json)
            value = float(payload.get('angle_deg', 90.0))
            return max(5.0, min(360.0, value))
        except Exception:
            return 90.0

    @staticmethod
    def _parse_words(text: str):
        if not text:
            return set()
        return {item.strip().lower() for item in str(text).split(',') if item.strip()}

    @staticmethod
    def _contains_any(text: str, words):
        tokens = set(text.split())
        for word in words:
            if ' ' in word:
                if word in text:
                    return True
            elif word in tokens:
                return True
        return False

    @staticmethod
    def _normalize_text(text: str):
        if not text:
            return ''
        text = str(text).strip().lower()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        return ' '.join(text.split())


def main(args=None):
    rclpy.init(args=args)
    node = RobotCommandExecutorNode()
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

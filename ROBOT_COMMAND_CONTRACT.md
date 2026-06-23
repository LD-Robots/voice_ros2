# Robot Command Contract (Voice → Motion Team)

This document is the integration contract between the **voice stack** and the
**motion team**. The voice stack only *recognizes* and *authorizes* commands; the
motion team *executes* them (one script per command). The voice stack never drives
any actuator (`cmd_vel`, services, etc.).

---

## 1. The flow

```
mic / STT / speaker-id
        │
        ▼
voice_command_node ──► /recognized_commands ──► robot_command_gate_node ──► /robot_commands ──► MOTION TEAM
   (recognizes)            (raw, internal)         (safety gate)              (approved)         (executes)
```

- `voice_command_node` recognizes one of the 5 commands from speech and publishes
  it on the **internal** topic `/recognized_commands`.
- `robot_command_gate_node` applies safety (confidence floor, spoken **stop**
  cancel, and confirmation for risky commands) and **only then** forwards the
  command to the motion team.
- **You (motion team) subscribe to the single topic `/robot_commands`** and switch
  on `command_id` to run the matching motion script.

---

## 2. Topics

| Topic | Type | Direction | Purpose |
|---|---|---|---|
| `/robot_commands` | `conversational_interfaces/RobotCommand` | voice → **motion** | Approved command (all 5). **Subscribe here and switch on `command_id`.** |
| `/robot_command_status` | `std_msgs/String` | both | Lifecycle. Voice publishes `dispatched:*`, `canceled`, `confirmation_*`. **Motion team should also publish progress here** (see §6). |
| `/recognized_commands` | `conversational_interfaces/RobotCommand` | internal | Raw recognizer output, **pre-safety**. Do **not** use it to drive the robot. |

> There is a single command bus: `/robot_commands`. All 5 commands arrive on it;
> dispatch by `command_id` (see §4). (No per-command topics.)

---

## 3. Message: `conversational_interfaces/RobotCommand`

```
std_msgs/Header header     # header.stamp = recognition time (use for tracing/correlation)
int32   command_id         # 1..5 (primary key — switch on this)
string  command_name       # move_forward | raise_hand | turn_arround | clap | say_hi
int32   steps              # number of steps; ONLY meaningful for move_forward, else 0
float32 confidence         # 0.0-1.0 recognition confidence (you may ignore)
string  parameters_json    # command-specific params, JSON object (see §4)
string  source_text        # original utterance (debug/logging)
string  language           # detected language code, e.g. "en", "ro"
string  speaker            # identified speaker label, or "Unknown"
```

---

## 4. The 5 commands

| id | command_name | meaning | relevant fields |
|---|---|---|---|
| 1 | `move_forward` | walk forward N steps | `steps` (≥1), `parameters_json`: `{"step_mode":"discrete","speed_scale":1.0}` |
| 2 | `raise_hand` | raise hand/arm | `parameters_json`: `{"motion":"upper_body","style":"default"}` |
| 3 | `turn_arround` | turn ~180° | `parameters_json`: `{"angle_deg":180,"speed_scale":1.0}` |
| 4 | `clap` | clap hands | `parameters_json`: `{"motion":"upper_body","style":"default"}` |
| 5 | `say_hi` | greeting gesture | `parameters_json`: `{"motion":"greeting","style":"default"}` |

Notes:
- `command_name` `turn_arround` is spelled this way deliberately — it is part of the
  frozen contract. Match the exact string / `command_id`.
- `parameters_json` is an extension point. New params may be added over time; parse
  it defensively (treat missing keys as defaults).

---

## 5. Safety behavior you can rely on

The gate guarantees that anything arriving on `/robot_commands`:

1. **Passed a confidence floor** (default 0.60).
2. **Was confirmed if risky.** Risky = `turn_arround` (≥150°) or `move_forward` with
   ≥5 steps. These reach you only *after* the speaker said "yes". You do not need to
   re-confirm.
3. **Can be canceled by voice.** If the user says **"stop"** (or `cancel/halt/opreste/
   anuleaza/stai`) the gate publishes `canceled` on `/robot_command_status`.
   **You must watch `/robot_command_status` for `canceled` and abort the running
   script.** This is the primary emergency-stop path.

---

## 6. `/robot_command_status` values

Published by the **voice stack**:

| value | meaning |
|---|---|
| `dispatched:<command_name>` | a command was just forwarded to you |
| `confirmation_required` | a risky command is waiting for "yes"/"no" (nothing sent yet) |
| `confirmation_accepted` | risky command confirmed (a `dispatched:*` follows) |
| `confirmation_rejected` / `confirmation_timeout` | risky command dropped |
| `canceled` | user said "stop" — **abort current execution** |

Recommended values for the **motion team** to publish back (free-form string):

| value | meaning |
|---|---|
| `executing:<command_name>` | started running the script |
| `completed:<command_name>` | finished successfully |
| `failed:<command_name>:<reason>` | execution failed |

(The conversational layer listens to this topic to mute the assistant during robot
interactions, so publishing progress improves the overall UX.)

---

## 7. Minimal subscriber example (rclpy)

```python
import json
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand
from std_msgs.msg import String


class MotionExecutor(Node):
    def __init__(self):
        super().__init__('motion_executor')
        self.create_subscription(RobotCommand, '/robot_commands', self.on_command, 10)
        self.create_subscription(String, '/robot_command_status', self.on_status, 10)
        self.status_pub = self.create_publisher(String, '/robot_command_status', 10)
        self._active = None

    def on_command(self, msg: RobotCommand):
        params = json.loads(msg.parameters_json or '{}')
        self._active = msg.command_name
        self._publish(f'executing:{msg.command_name}')
        try:
            if msg.command_id == 1:    # move_forward
                self.move_forward(steps=msg.steps, speed=params.get('speed_scale', 1.0))
            elif msg.command_id == 2:  # raise_hand
                self.raise_hand()
            elif msg.command_id == 3:  # turn_arround
                self.turn(angle_deg=params.get('angle_deg', 180))
            elif msg.command_id == 4:  # clap
                self.clap()
            elif msg.command_id == 5:  # say_hi
                self.say_hi()
            self._publish(f'completed:{msg.command_name}')
        except Exception as exc:
            self._publish(f'failed:{msg.command_name}:{exc}')
        finally:
            self._active = None

    def on_status(self, msg: String):
        if msg.data == 'canceled' and self._active is not None:
            self.abort_current_motion()   # <-- implement: stop the running script NOW

    def _publish(self, text):
        m = String(); m.data = text; self.status_pub.publish(m)

    # implement these against your hardware:
    def move_forward(self, steps, speed): ...
    def raise_hand(self): ...
    def turn(self, angle_deg): ...
    def clap(self): ...
    def say_hi(self): ...
    def abort_current_motion(self): ...
```

---

## 8. Quick manual testing (no robot needed)

Watch what the motion team would receive:

```bash
ros2 topic echo /robot_commands
ros2 topic echo /robot_command_status
```

Inject a command without speaking (simulate the gate output):

```bash
ros2 topic pub --once /robot_commands conversational_interfaces/msg/RobotCommand \
  "{command_id: 4, command_name: 'clap', steps: 0, confidence: 0.95, parameters_json: '{}'}"
```

Simulate an emergency stop:

```bash
ros2 topic pub --once /robot_command_status std_msgs/msg/String "{data: 'canceled'}"
```

---

## 9. Ownership boundary

| Concern | Owner |
|---|---|
| Speech → command recognition | voice stack (`voice_command_node`) |
| Confidence / stop-cancel / risky-confirmation | voice stack (`robot_command_gate_node`) |
| Actual robot motion / actuators | **motion team** |
| Reporting execution progress on `/robot_command_status` | **motion team** |

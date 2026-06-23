std_msgs/Header header

# Public motion-team command contract.
# Published on the single bus /voice/robot_commands AFTER the safety gate.
# The motion team subscribes there and runs one script per command (switch on command_id).
# Only these 5 commands are ever published:
int32 command_id           # 1=move_forward, 2=raise_hand, 3=turn_arround, 4=clap, 5=say_hi
string command_name        # move_forward | raise_hand | turn_arround | clap | say_hi

# Command parameters
int32 steps                # number of steps; only meaningful for move_forward (id=1), else 0
float32 confidence         # 0.0-1.0 recognition confidence
string parameters_json     # command-specific params, e.g. {"angle_deg":180} for turn_arround

# Context from the voice pipeline (logging / attribution only)
string source_text         # original utterance
string language            # detected language code, e.g. "en", "ro"
string speaker             # identified speaker label, or "Unknown"

std_msgs/Header header

# Normalized voice command intent
string intent              # stop | move | turn | raise_hands | lower_hands | wave | dance | sit | stand
string direction           # forward | backward | left | right | none
int32 command_id           # stable hardware command ID; -1 when unknown
string command_name        # stable hardware command name, e.g. move_forward
int32 steps                # 0 when not used
float32 confidence         # 0.0-1.0

# Original context from voice pipeline
string source_text
string language
string speaker

# JSON object for future actuator/planner parameters
string parameters_json

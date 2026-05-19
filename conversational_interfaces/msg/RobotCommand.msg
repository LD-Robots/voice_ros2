std_msgs/Header header

# Normalized voice command intent and stable numeric code for external scripts
string intent              # stop | move | turn | handshake | raise_hands | lower_hands | wave | dance
int32 command_id           # Stable command code, e.g. handshake=1
string direction           # forward | backward | left | right | none
int32 steps                # 0 when not used
float32 confidence         # 0.0-1.0

# Original context from voice pipeline
string source_text
string language
string speaker

# JSON object for future actuator/planner parameters
string parameters_json

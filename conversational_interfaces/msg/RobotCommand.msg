std_msgs/Header header

# Normalized voice command intent
string intent              # raise_hands | move | dance
string direction           # forward | backward | none
int32 steps                # 0 when not used
float32 confidence         # 0.0-1.0

# Original context from voice pipeline
string source_text
string language
string speaker

# JSON object for future actuator/planner parameters
string parameters_json

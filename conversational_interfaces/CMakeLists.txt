cmake_minimum_required(VERSION 3.8)
project(conversational_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/HumanoidMotion.action"
  "msg/Audio.msg"
  "msg/RobotCommand.msg"
  "msg/Transcription.msg"
  "msg/TextChunk.msg"
  "msg/WakeWord.msg"
  "srv/SetXmosParam.srv"
  DEPENDENCIES std_msgs action_msgs
)

ament_package()

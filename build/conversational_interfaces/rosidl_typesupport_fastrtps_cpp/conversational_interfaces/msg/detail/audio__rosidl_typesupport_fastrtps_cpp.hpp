// generated from rosidl_typesupport_fastrtps_cpp/resource/idl__rosidl_typesupport_fastrtps_cpp.hpp.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__ROSIDL_TYPESUPPORT_FASTRTPS_CPP_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__ROSIDL_TYPESUPPORT_FASTRTPS_CPP_HPP_

#include <cstddef>
#include "rosidl_runtime_c/message_type_support_struct.h"
#include "rosidl_typesupport_interface/macros.h"
#include "conversational_interfaces/msg/rosidl_typesupport_fastrtps_cpp__visibility_control.h"
#include "conversational_interfaces/msg/detail/audio__struct.hpp"

#ifndef _WIN32
# pragma GCC diagnostic push
# pragma GCC diagnostic ignored "-Wunused-parameter"
# ifdef __clang__
#  pragma clang diagnostic ignored "-Wdeprecated-register"
#  pragma clang diagnostic ignored "-Wreturn-type-c-linkage"
# endif
#endif
#ifndef _WIN32
# pragma GCC diagnostic pop
#endif

#include "fastcdr/Cdr.h"

namespace conversational_interfaces
{

namespace msg
{

namespace typesupport_fastrtps_cpp
{

bool
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
cdr_serialize(
  const conversational_interfaces::msg::Audio & ros_message,
  eprosima::fastcdr::Cdr & cdr);

bool
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
cdr_deserialize(
  eprosima::fastcdr::Cdr & cdr,
  conversational_interfaces::msg::Audio & ros_message);

size_t
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
get_serialized_size(
  const conversational_interfaces::msg::Audio & ros_message,
  size_t current_alignment);

size_t
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
max_serialized_size_Audio(
  bool & full_bounded,
  bool & is_plain,
  size_t current_alignment);

bool
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
cdr_serialize_key(
  const conversational_interfaces::msg::Audio & ros_message,
  eprosima::fastcdr::Cdr &);

size_t
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
get_serialized_size_key(
  const conversational_interfaces::msg::Audio & ros_message,
  size_t current_alignment);

size_t
ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
max_serialized_size_key_Audio(
  bool & full_bounded,
  bool & is_plain,
  size_t current_alignment);

}  // namespace typesupport_fastrtps_cpp

}  // namespace msg

}  // namespace conversational_interfaces

#ifdef __cplusplus
extern "C"
{
#endif

ROSIDL_TYPESUPPORT_FASTRTPS_CPP_PUBLIC_conversational_interfaces
const rosidl_message_type_support_t *
  ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_fastrtps_cpp, conversational_interfaces, msg, Audio)();

#ifdef __cplusplus
}
#endif

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__ROSIDL_TYPESUPPORT_FASTRTPS_CPP_HPP_

// generated from rosidl_typesupport_fastrtps_c/resource/idl__rosidl_typesupport_fastrtps_c.h.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice
#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__ROSIDL_TYPESUPPORT_FASTRTPS_C_H_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__ROSIDL_TYPESUPPORT_FASTRTPS_C_H_


#include <stddef.h>
#include "rosidl_runtime_c/message_type_support_struct.h"
#include "rosidl_typesupport_interface/macros.h"
#include "conversational_interfaces/msg/rosidl_typesupport_fastrtps_c__visibility_control.h"
#include "conversational_interfaces/msg/detail/transcription__struct.h"
#include "fastcdr/Cdr.h"

#ifdef __cplusplus
extern "C"
{
#endif

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
bool cdr_serialize_conversational_interfaces__msg__Transcription(
  const conversational_interfaces__msg__Transcription * ros_message,
  eprosima::fastcdr::Cdr & cdr);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
bool cdr_deserialize_conversational_interfaces__msg__Transcription(
  eprosima::fastcdr::Cdr &,
  conversational_interfaces__msg__Transcription * ros_message);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
size_t get_serialized_size_conversational_interfaces__msg__Transcription(
  const void * untyped_ros_message,
  size_t current_alignment);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
size_t max_serialized_size_conversational_interfaces__msg__Transcription(
  bool & full_bounded,
  bool & is_plain,
  size_t current_alignment);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
bool cdr_serialize_key_conversational_interfaces__msg__Transcription(
  const conversational_interfaces__msg__Transcription * ros_message,
  eprosima::fastcdr::Cdr & cdr);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
size_t get_serialized_size_key_conversational_interfaces__msg__Transcription(
  const void * untyped_ros_message,
  size_t current_alignment);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
size_t max_serialized_size_key_conversational_interfaces__msg__Transcription(
  bool & full_bounded,
  bool & is_plain,
  size_t current_alignment);

ROSIDL_TYPESUPPORT_FASTRTPS_C_PUBLIC_conversational_interfaces
const rosidl_message_type_support_t *
ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_fastrtps_c, conversational_interfaces, msg, Transcription)();

#ifdef __cplusplus
}
#endif

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__ROSIDL_TYPESUPPORT_FASTRTPS_C_H_

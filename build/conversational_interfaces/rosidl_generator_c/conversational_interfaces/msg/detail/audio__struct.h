// generated from rosidl_generator_c/resource/idl__struct.h.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/audio.h"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__STRUCT_H_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__STRUCT_H_

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Constants defined in the message

// Include directives for member types
// Member 'data'
#include "rosidl_runtime_c/primitives_sequence.h"

/// Struct defined in msg/Audio in the package conversational_interfaces.
typedef struct conversational_interfaces__msg__Audio
{
  uint32_t sample_rate;
  uint32_t channels;
  rosidl_runtime_c__int16__Sequence data;
} conversational_interfaces__msg__Audio;

// Struct for a sequence of conversational_interfaces__msg__Audio.
typedef struct conversational_interfaces__msg__Audio__Sequence
{
  conversational_interfaces__msg__Audio * data;
  /// The number of valid items in data
  size_t size;
  /// The number of allocated items in data
  size_t capacity;
} conversational_interfaces__msg__Audio__Sequence;

#ifdef __cplusplus
}
#endif

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__STRUCT_H_

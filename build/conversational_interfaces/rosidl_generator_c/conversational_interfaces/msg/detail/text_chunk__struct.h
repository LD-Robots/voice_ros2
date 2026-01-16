// generated from rosidl_generator_c/resource/idl__struct.h.em
// with input from conversational_interfaces:msg/TextChunk.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/text_chunk.h"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_H_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_H_

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Constants defined in the message

// Include directives for member types
// Member 'text'
// Member 'language'
// Member 'session_id'
#include "rosidl_runtime_c/string.h"

/// Struct defined in msg/TextChunk in the package conversational_interfaces.
typedef struct conversational_interfaces__msg__TextChunk
{
  rosidl_runtime_c__String text;
  rosidl_runtime_c__String language;
  bool is_final;
  rosidl_runtime_c__String session_id;
} conversational_interfaces__msg__TextChunk;

// Struct for a sequence of conversational_interfaces__msg__TextChunk.
typedef struct conversational_interfaces__msg__TextChunk__Sequence
{
  conversational_interfaces__msg__TextChunk * data;
  /// The number of valid items in data
  size_t size;
  /// The number of allocated items in data
  size_t capacity;
} conversational_interfaces__msg__TextChunk__Sequence;

#ifdef __cplusplus
}
#endif

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_H_

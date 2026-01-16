// generated from rosidl_typesupport_introspection_c/resource/idl__type_support.c.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

#include <stddef.h>
#include "conversational_interfaces/msg/detail/transcription__rosidl_typesupport_introspection_c.h"
#include "conversational_interfaces/msg/rosidl_typesupport_introspection_c__visibility_control.h"
#include "rosidl_typesupport_introspection_c/field_types.h"
#include "rosidl_typesupport_introspection_c/identifier.h"
#include "rosidl_typesupport_introspection_c/message_introspection.h"
#include "conversational_interfaces/msg/detail/transcription__functions.h"
#include "conversational_interfaces/msg/detail/transcription__struct.h"


// Include directives for member types
// Member `text`
// Member `language`
#include "rosidl_runtime_c/string_functions.h"

#ifdef __cplusplus
extern "C"
{
#endif

void conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_init_function(
  void * message_memory, enum rosidl_runtime_c__message_initialization _init)
{
  // TODO(karsten1987): initializers are not yet implemented for typesupport c
  // see https://github.com/ros2/ros2/issues/397
  (void) _init;
  conversational_interfaces__msg__Transcription__init(message_memory);
}

void conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_fini_function(void * message_memory)
{
  conversational_interfaces__msg__Transcription__fini(message_memory);
}

static rosidl_typesupport_introspection_c__MessageMember conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_member_array[3] = {
  {
    "text",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_STRING,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Transcription, text),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "language",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_STRING,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Transcription, language),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "confidence",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_FLOAT,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Transcription, confidence),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  }
};

static const rosidl_typesupport_introspection_c__MessageMembers conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_members = {
  "conversational_interfaces__msg",  // message namespace
  "Transcription",  // message name
  3,  // number of fields
  sizeof(conversational_interfaces__msg__Transcription),
  false,  // has_any_key_member_
  conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_member_array,  // message members
  conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_init_function,  // function to initialize message memory (memory has to be allocated)
  conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_fini_function  // function to terminate message instance (will not free memory)
};

// this is not const since it must be initialized on first access
// since C does not allow non-integral compile-time constants
static rosidl_message_type_support_t conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_type_support_handle = {
  0,
  &conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_members,
  get_message_typesupport_handle_function,
  &conversational_interfaces__msg__Transcription__get_type_hash,
  &conversational_interfaces__msg__Transcription__get_type_description,
  &conversational_interfaces__msg__Transcription__get_type_description_sources,
};

ROSIDL_TYPESUPPORT_INTROSPECTION_C_EXPORT_conversational_interfaces
const rosidl_message_type_support_t *
ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_introspection_c, conversational_interfaces, msg, Transcription)() {
  if (!conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_type_support_handle.typesupport_identifier) {
    conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_type_support_handle.typesupport_identifier =
      rosidl_typesupport_introspection_c__identifier;
  }
  return &conversational_interfaces__msg__Transcription__rosidl_typesupport_introspection_c__Transcription_message_type_support_handle;
}
#ifdef __cplusplus
}
#endif

// generated from rosidl_typesupport_introspection_c/resource/idl__type_support.c.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

#include <stddef.h>
#include "conversational_interfaces/msg/detail/audio__rosidl_typesupport_introspection_c.h"
#include "conversational_interfaces/msg/rosidl_typesupport_introspection_c__visibility_control.h"
#include "rosidl_typesupport_introspection_c/field_types.h"
#include "rosidl_typesupport_introspection_c/identifier.h"
#include "rosidl_typesupport_introspection_c/message_introspection.h"
#include "conversational_interfaces/msg/detail/audio__functions.h"
#include "conversational_interfaces/msg/detail/audio__struct.h"


// Include directives for member types
// Member `data`
#include "rosidl_runtime_c/primitives_sequence_functions.h"

#ifdef __cplusplus
extern "C"
{
#endif

void conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_init_function(
  void * message_memory, enum rosidl_runtime_c__message_initialization _init)
{
  // TODO(karsten1987): initializers are not yet implemented for typesupport c
  // see https://github.com/ros2/ros2/issues/397
  (void) _init;
  conversational_interfaces__msg__Audio__init(message_memory);
}

void conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_fini_function(void * message_memory)
{
  conversational_interfaces__msg__Audio__fini(message_memory);
}

size_t conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__size_function__Audio__data(
  const void * untyped_member)
{
  const rosidl_runtime_c__int16__Sequence * member =
    (const rosidl_runtime_c__int16__Sequence *)(untyped_member);
  return member->size;
}

const void * conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_const_function__Audio__data(
  const void * untyped_member, size_t index)
{
  const rosidl_runtime_c__int16__Sequence * member =
    (const rosidl_runtime_c__int16__Sequence *)(untyped_member);
  return &member->data[index];
}

void * conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_function__Audio__data(
  void * untyped_member, size_t index)
{
  rosidl_runtime_c__int16__Sequence * member =
    (rosidl_runtime_c__int16__Sequence *)(untyped_member);
  return &member->data[index];
}

void conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__fetch_function__Audio__data(
  const void * untyped_member, size_t index, void * untyped_value)
{
  const int16_t * item =
    ((const int16_t *)
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_const_function__Audio__data(untyped_member, index));
  int16_t * value =
    (int16_t *)(untyped_value);
  *value = *item;
}

void conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__assign_function__Audio__data(
  void * untyped_member, size_t index, const void * untyped_value)
{
  int16_t * item =
    ((int16_t *)
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_function__Audio__data(untyped_member, index));
  const int16_t * value =
    (const int16_t *)(untyped_value);
  *item = *value;
}

bool conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__resize_function__Audio__data(
  void * untyped_member, size_t size)
{
  rosidl_runtime_c__int16__Sequence * member =
    (rosidl_runtime_c__int16__Sequence *)(untyped_member);
  rosidl_runtime_c__int16__Sequence__fini(member);
  return rosidl_runtime_c__int16__Sequence__init(member, size);
}

static rosidl_typesupport_introspection_c__MessageMember conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_member_array[3] = {
  {
    "sample_rate",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_UINT32,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Audio, sample_rate),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "channels",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_UINT32,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Audio, channels),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "data",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_INT16,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is key
    true,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces__msg__Audio, data),  // bytes offset in struct
    NULL,  // default value
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__size_function__Audio__data,  // size() function pointer
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_const_function__Audio__data,  // get_const(index) function pointer
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__get_function__Audio__data,  // get(index) function pointer
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__fetch_function__Audio__data,  // fetch(index, &value) function pointer
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__assign_function__Audio__data,  // assign(index, value) function pointer
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__resize_function__Audio__data  // resize(index) function pointer
  }
};

static const rosidl_typesupport_introspection_c__MessageMembers conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_members = {
  "conversational_interfaces__msg",  // message namespace
  "Audio",  // message name
  3,  // number of fields
  sizeof(conversational_interfaces__msg__Audio),
  false,  // has_any_key_member_
  conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_member_array,  // message members
  conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_init_function,  // function to initialize message memory (memory has to be allocated)
  conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_fini_function  // function to terminate message instance (will not free memory)
};

// this is not const since it must be initialized on first access
// since C does not allow non-integral compile-time constants
static rosidl_message_type_support_t conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_type_support_handle = {
  0,
  &conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_members,
  get_message_typesupport_handle_function,
  &conversational_interfaces__msg__Audio__get_type_hash,
  &conversational_interfaces__msg__Audio__get_type_description,
  &conversational_interfaces__msg__Audio__get_type_description_sources,
};

ROSIDL_TYPESUPPORT_INTROSPECTION_C_EXPORT_conversational_interfaces
const rosidl_message_type_support_t *
ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_introspection_c, conversational_interfaces, msg, Audio)() {
  if (!conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_type_support_handle.typesupport_identifier) {
    conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_type_support_handle.typesupport_identifier =
      rosidl_typesupport_introspection_c__identifier;
  }
  return &conversational_interfaces__msg__Audio__rosidl_typesupport_introspection_c__Audio_message_type_support_handle;
}
#ifdef __cplusplus
}
#endif

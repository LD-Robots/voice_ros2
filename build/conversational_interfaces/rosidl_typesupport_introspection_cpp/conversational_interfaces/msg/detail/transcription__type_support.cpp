// generated from rosidl_typesupport_introspection_cpp/resource/idl__type_support.cpp.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

#include "array"
#include "cstddef"
#include "string"
#include "vector"
#include "rosidl_runtime_c/message_type_support_struct.h"
#include "rosidl_typesupport_cpp/message_type_support.hpp"
#include "rosidl_typesupport_interface/macros.h"
#include "conversational_interfaces/msg/detail/transcription__functions.h"
#include "conversational_interfaces/msg/detail/transcription__struct.hpp"
#include "rosidl_typesupport_introspection_cpp/field_types.hpp"
#include "rosidl_typesupport_introspection_cpp/identifier.hpp"
#include "rosidl_typesupport_introspection_cpp/message_introspection.hpp"
#include "rosidl_typesupport_introspection_cpp/message_type_support_decl.hpp"
#include "rosidl_typesupport_introspection_cpp/visibility_control.h"

namespace conversational_interfaces
{

namespace msg
{

namespace rosidl_typesupport_introspection_cpp
{

void Transcription_init_function(
  void * message_memory, rosidl_runtime_cpp::MessageInitialization _init)
{
  new (message_memory) conversational_interfaces::msg::Transcription(_init);
}

void Transcription_fini_function(void * message_memory)
{
  auto typed_message = static_cast<conversational_interfaces::msg::Transcription *>(message_memory);
  typed_message->~Transcription();
}

static const ::rosidl_typesupport_introspection_cpp::MessageMember Transcription_message_member_array[3] = {
  {
    "text",  // name
    ::rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING,  // type
    0,  // upper bound of string
    nullptr,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces::msg::Transcription, text),  // bytes offset in struct
    nullptr,  // default value
    nullptr,  // size() function pointer
    nullptr,  // get_const(index) function pointer
    nullptr,  // get(index) function pointer
    nullptr,  // fetch(index, &value) function pointer
    nullptr,  // assign(index, value) function pointer
    nullptr  // resize(index) function pointer
  },
  {
    "language",  // name
    ::rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING,  // type
    0,  // upper bound of string
    nullptr,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces::msg::Transcription, language),  // bytes offset in struct
    nullptr,  // default value
    nullptr,  // size() function pointer
    nullptr,  // get_const(index) function pointer
    nullptr,  // get(index) function pointer
    nullptr,  // fetch(index, &value) function pointer
    nullptr,  // assign(index, value) function pointer
    nullptr  // resize(index) function pointer
  },
  {
    "confidence",  // name
    ::rosidl_typesupport_introspection_cpp::ROS_TYPE_FLOAT,  // type
    0,  // upper bound of string
    nullptr,  // members of sub message
    false,  // is key
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(conversational_interfaces::msg::Transcription, confidence),  // bytes offset in struct
    nullptr,  // default value
    nullptr,  // size() function pointer
    nullptr,  // get_const(index) function pointer
    nullptr,  // get(index) function pointer
    nullptr,  // fetch(index, &value) function pointer
    nullptr,  // assign(index, value) function pointer
    nullptr  // resize(index) function pointer
  }
};

static const ::rosidl_typesupport_introspection_cpp::MessageMembers Transcription_message_members = {
  "conversational_interfaces::msg",  // message namespace
  "Transcription",  // message name
  3,  // number of fields
  sizeof(conversational_interfaces::msg::Transcription),
  false,  // has_any_key_member_
  Transcription_message_member_array,  // message members
  Transcription_init_function,  // function to initialize message memory (memory has to be allocated)
  Transcription_fini_function  // function to terminate message instance (will not free memory)
};

static const rosidl_message_type_support_t Transcription_message_type_support_handle = {
  ::rosidl_typesupport_introspection_cpp::typesupport_identifier,
  &Transcription_message_members,
  get_message_typesupport_handle_function,
  &conversational_interfaces__msg__Transcription__get_type_hash,
  &conversational_interfaces__msg__Transcription__get_type_description,
  &conversational_interfaces__msg__Transcription__get_type_description_sources,
};

}  // namespace rosidl_typesupport_introspection_cpp

}  // namespace msg

}  // namespace conversational_interfaces


namespace rosidl_typesupport_introspection_cpp
{

template<>
ROSIDL_TYPESUPPORT_INTROSPECTION_CPP_PUBLIC
const rosidl_message_type_support_t *
get_message_type_support_handle<conversational_interfaces::msg::Transcription>()
{
  return &::conversational_interfaces::msg::rosidl_typesupport_introspection_cpp::Transcription_message_type_support_handle;
}

}  // namespace rosidl_typesupport_introspection_cpp

#ifdef __cplusplus
extern "C"
{
#endif

ROSIDL_TYPESUPPORT_INTROSPECTION_CPP_PUBLIC
const rosidl_message_type_support_t *
ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_introspection_cpp, conversational_interfaces, msg, Transcription)() {
  return &::conversational_interfaces::msg::rosidl_typesupport_introspection_cpp::Transcription_message_type_support_handle;
}

#ifdef __cplusplus
}
#endif

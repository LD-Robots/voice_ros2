// generated from rosidl_generator_c/resource/idl__description.c.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

#include "conversational_interfaces/msg/detail/audio__functions.h"

ROSIDL_GENERATOR_C_PUBLIC_conversational_interfaces
const rosidl_type_hash_t *
conversational_interfaces__msg__Audio__get_type_hash(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_type_hash_t hash = {1, {
      0xd1, 0xb6, 0x3f, 0x5c, 0xb0, 0x8f, 0x76, 0xd2,
      0xeb, 0x3c, 0xf7, 0xca, 0x4f, 0xc4, 0x81, 0x83,
      0x49, 0x7f, 0x91, 0xb3, 0xc0, 0xf8, 0xf5, 0x1e,
      0x42, 0xb2, 0xbc, 0xaa, 0x24, 0xab, 0xe9, 0x31,
    }};
  return &hash;
}

#include <assert.h>
#include <string.h>

// Include directives for referenced types

// Hashes for external referenced types
#ifndef NDEBUG
#endif

static char conversational_interfaces__msg__Audio__TYPE_NAME[] = "conversational_interfaces/msg/Audio";

// Define type names, field names, and default values
static char conversational_interfaces__msg__Audio__FIELD_NAME__sample_rate[] = "sample_rate";
static char conversational_interfaces__msg__Audio__FIELD_NAME__channels[] = "channels";
static char conversational_interfaces__msg__Audio__FIELD_NAME__data[] = "data";

static rosidl_runtime_c__type_description__Field conversational_interfaces__msg__Audio__FIELDS[] = {
  {
    {conversational_interfaces__msg__Audio__FIELD_NAME__sample_rate, 11, 11},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_UINT32,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__Audio__FIELD_NAME__channels, 8, 8},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_UINT32,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__Audio__FIELD_NAME__data, 4, 4},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_INT16_UNBOUNDED_SEQUENCE,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
};

const rosidl_runtime_c__type_description__TypeDescription *
conversational_interfaces__msg__Audio__get_type_description(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static bool constructed = false;
  static const rosidl_runtime_c__type_description__TypeDescription description = {
    {
      {conversational_interfaces__msg__Audio__TYPE_NAME, 35, 35},
      {conversational_interfaces__msg__Audio__FIELDS, 3, 3},
    },
    {NULL, 0, 0},
  };
  if (!constructed) {
    constructed = true;
  }
  return &description;
}

static char toplevel_type_raw_source[] =
  "uint32 sample_rate\n"
  "uint32 channels\n"
  "int16[] data";

static char msg_encoding[] = "msg";

// Define all individual source functions

const rosidl_runtime_c__type_description__TypeSource *
conversational_interfaces__msg__Audio__get_individual_type_description_source(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static const rosidl_runtime_c__type_description__TypeSource source = {
    {conversational_interfaces__msg__Audio__TYPE_NAME, 35, 35},
    {msg_encoding, 3, 3},
    {toplevel_type_raw_source, 48, 48},
  };
  return &source;
}

const rosidl_runtime_c__type_description__TypeSource__Sequence *
conversational_interfaces__msg__Audio__get_type_description_sources(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_runtime_c__type_description__TypeSource sources[1];
  static const rosidl_runtime_c__type_description__TypeSource__Sequence source_sequence = {sources, 1, 1};
  static bool constructed = false;
  if (!constructed) {
    sources[0] = *conversational_interfaces__msg__Audio__get_individual_type_description_source(NULL),
    constructed = true;
  }
  return &source_sequence;
}

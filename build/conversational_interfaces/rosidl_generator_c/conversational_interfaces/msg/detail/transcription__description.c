// generated from rosidl_generator_c/resource/idl__description.c.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

#include "conversational_interfaces/msg/detail/transcription__functions.h"

ROSIDL_GENERATOR_C_PUBLIC_conversational_interfaces
const rosidl_type_hash_t *
conversational_interfaces__msg__Transcription__get_type_hash(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_type_hash_t hash = {1, {
      0x16, 0x25, 0x7f, 0xa0, 0xf1, 0x69, 0x04, 0x2c,
      0x20, 0x04, 0x8b, 0x21, 0x17, 0x76, 0xf7, 0x68,
      0x9a, 0x3d, 0x59, 0xa1, 0xfc, 0xe8, 0xca, 0x71,
      0x8b, 0x59, 0xb0, 0xfc, 0x82, 0x16, 0x7e, 0x2f,
    }};
  return &hash;
}

#include <assert.h>
#include <string.h>

// Include directives for referenced types

// Hashes for external referenced types
#ifndef NDEBUG
#endif

static char conversational_interfaces__msg__Transcription__TYPE_NAME[] = "conversational_interfaces/msg/Transcription";

// Define type names, field names, and default values
static char conversational_interfaces__msg__Transcription__FIELD_NAME__text[] = "text";
static char conversational_interfaces__msg__Transcription__FIELD_NAME__language[] = "language";
static char conversational_interfaces__msg__Transcription__FIELD_NAME__confidence[] = "confidence";

static rosidl_runtime_c__type_description__Field conversational_interfaces__msg__Transcription__FIELDS[] = {
  {
    {conversational_interfaces__msg__Transcription__FIELD_NAME__text, 4, 4},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_STRING,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__Transcription__FIELD_NAME__language, 8, 8},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_STRING,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__Transcription__FIELD_NAME__confidence, 10, 10},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_FLOAT,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
};

const rosidl_runtime_c__type_description__TypeDescription *
conversational_interfaces__msg__Transcription__get_type_description(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static bool constructed = false;
  static const rosidl_runtime_c__type_description__TypeDescription description = {
    {
      {conversational_interfaces__msg__Transcription__TYPE_NAME, 43, 43},
      {conversational_interfaces__msg__Transcription__FIELDS, 3, 3},
    },
    {NULL, 0, 0},
  };
  if (!constructed) {
    constructed = true;
  }
  return &description;
}

static char toplevel_type_raw_source[] =
  "string text\n"
  "string language\n"
  "float32 confidence";

static char msg_encoding[] = "msg";

// Define all individual source functions

const rosidl_runtime_c__type_description__TypeSource *
conversational_interfaces__msg__Transcription__get_individual_type_description_source(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static const rosidl_runtime_c__type_description__TypeSource source = {
    {conversational_interfaces__msg__Transcription__TYPE_NAME, 43, 43},
    {msg_encoding, 3, 3},
    {toplevel_type_raw_source, 47, 47},
  };
  return &source;
}

const rosidl_runtime_c__type_description__TypeSource__Sequence *
conversational_interfaces__msg__Transcription__get_type_description_sources(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_runtime_c__type_description__TypeSource sources[1];
  static const rosidl_runtime_c__type_description__TypeSource__Sequence source_sequence = {sources, 1, 1};
  static bool constructed = false;
  if (!constructed) {
    sources[0] = *conversational_interfaces__msg__Transcription__get_individual_type_description_source(NULL),
    constructed = true;
  }
  return &source_sequence;
}

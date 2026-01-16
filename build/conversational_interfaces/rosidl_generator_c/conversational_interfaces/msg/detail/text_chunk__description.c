// generated from rosidl_generator_c/resource/idl__description.c.em
// with input from conversational_interfaces:msg/TextChunk.idl
// generated code does not contain a copyright notice

#include "conversational_interfaces/msg/detail/text_chunk__functions.h"

ROSIDL_GENERATOR_C_PUBLIC_conversational_interfaces
const rosidl_type_hash_t *
conversational_interfaces__msg__TextChunk__get_type_hash(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_type_hash_t hash = {1, {
      0x7e, 0xf8, 0x3f, 0x3d, 0x59, 0x4d, 0xbd, 0x79,
      0x8f, 0x8b, 0xa1, 0xdc, 0x67, 0xc9, 0xae, 0x6e,
      0xd5, 0xdc, 0x32, 0xde, 0x49, 0x96, 0x90, 0xb9,
      0x6e, 0xb7, 0x06, 0x05, 0xe3, 0x0a, 0xd3, 0x19,
    }};
  return &hash;
}

#include <assert.h>
#include <string.h>

// Include directives for referenced types

// Hashes for external referenced types
#ifndef NDEBUG
#endif

static char conversational_interfaces__msg__TextChunk__TYPE_NAME[] = "conversational_interfaces/msg/TextChunk";

// Define type names, field names, and default values
static char conversational_interfaces__msg__TextChunk__FIELD_NAME__text[] = "text";
static char conversational_interfaces__msg__TextChunk__FIELD_NAME__language[] = "language";
static char conversational_interfaces__msg__TextChunk__FIELD_NAME__is_final[] = "is_final";
static char conversational_interfaces__msg__TextChunk__FIELD_NAME__session_id[] = "session_id";

static rosidl_runtime_c__type_description__Field conversational_interfaces__msg__TextChunk__FIELDS[] = {
  {
    {conversational_interfaces__msg__TextChunk__FIELD_NAME__text, 4, 4},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_STRING,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__TextChunk__FIELD_NAME__language, 8, 8},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_STRING,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__TextChunk__FIELD_NAME__is_final, 8, 8},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_BOOLEAN,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
  {
    {conversational_interfaces__msg__TextChunk__FIELD_NAME__session_id, 10, 10},
    {
      rosidl_runtime_c__type_description__FieldType__FIELD_TYPE_STRING,
      0,
      0,
      {NULL, 0, 0},
    },
    {NULL, 0, 0},
  },
};

const rosidl_runtime_c__type_description__TypeDescription *
conversational_interfaces__msg__TextChunk__get_type_description(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static bool constructed = false;
  static const rosidl_runtime_c__type_description__TypeDescription description = {
    {
      {conversational_interfaces__msg__TextChunk__TYPE_NAME, 39, 39},
      {conversational_interfaces__msg__TextChunk__FIELDS, 4, 4},
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
  "bool is_final\n"
  "string session_id";

static char msg_encoding[] = "msg";

// Define all individual source functions

const rosidl_runtime_c__type_description__TypeSource *
conversational_interfaces__msg__TextChunk__get_individual_type_description_source(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static const rosidl_runtime_c__type_description__TypeSource source = {
    {conversational_interfaces__msg__TextChunk__TYPE_NAME, 39, 39},
    {msg_encoding, 3, 3},
    {toplevel_type_raw_source, 60, 60},
  };
  return &source;
}

const rosidl_runtime_c__type_description__TypeSource__Sequence *
conversational_interfaces__msg__TextChunk__get_type_description_sources(
  const rosidl_message_type_support_t * type_support)
{
  (void)type_support;
  static rosidl_runtime_c__type_description__TypeSource sources[1];
  static const rosidl_runtime_c__type_description__TypeSource__Sequence source_sequence = {sources, 1, 1};
  static bool constructed = false;
  if (!constructed) {
    sources[0] = *conversational_interfaces__msg__TextChunk__get_individual_type_description_source(NULL),
    constructed = true;
  }
  return &source_sequence;
}

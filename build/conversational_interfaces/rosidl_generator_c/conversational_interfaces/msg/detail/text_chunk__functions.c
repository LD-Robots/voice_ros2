// generated from rosidl_generator_c/resource/idl__functions.c.em
// with input from conversational_interfaces:msg/TextChunk.idl
// generated code does not contain a copyright notice
#include "conversational_interfaces/msg/detail/text_chunk__functions.h"

#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "rcutils/allocator.h"


// Include directives for member types
// Member `text`
// Member `language`
// Member `session_id`
#include "rosidl_runtime_c/string_functions.h"

bool
conversational_interfaces__msg__TextChunk__init(conversational_interfaces__msg__TextChunk * msg)
{
  if (!msg) {
    return false;
  }
  // text
  if (!rosidl_runtime_c__String__init(&msg->text)) {
    conversational_interfaces__msg__TextChunk__fini(msg);
    return false;
  }
  // language
  if (!rosidl_runtime_c__String__init(&msg->language)) {
    conversational_interfaces__msg__TextChunk__fini(msg);
    return false;
  }
  // is_final
  // session_id
  if (!rosidl_runtime_c__String__init(&msg->session_id)) {
    conversational_interfaces__msg__TextChunk__fini(msg);
    return false;
  }
  return true;
}

void
conversational_interfaces__msg__TextChunk__fini(conversational_interfaces__msg__TextChunk * msg)
{
  if (!msg) {
    return;
  }
  // text
  rosidl_runtime_c__String__fini(&msg->text);
  // language
  rosidl_runtime_c__String__fini(&msg->language);
  // is_final
  // session_id
  rosidl_runtime_c__String__fini(&msg->session_id);
}

bool
conversational_interfaces__msg__TextChunk__are_equal(const conversational_interfaces__msg__TextChunk * lhs, const conversational_interfaces__msg__TextChunk * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  // text
  if (!rosidl_runtime_c__String__are_equal(
      &(lhs->text), &(rhs->text)))
  {
    return false;
  }
  // language
  if (!rosidl_runtime_c__String__are_equal(
      &(lhs->language), &(rhs->language)))
  {
    return false;
  }
  // is_final
  if (lhs->is_final != rhs->is_final) {
    return false;
  }
  // session_id
  if (!rosidl_runtime_c__String__are_equal(
      &(lhs->session_id), &(rhs->session_id)))
  {
    return false;
  }
  return true;
}

bool
conversational_interfaces__msg__TextChunk__copy(
  const conversational_interfaces__msg__TextChunk * input,
  conversational_interfaces__msg__TextChunk * output)
{
  if (!input || !output) {
    return false;
  }
  // text
  if (!rosidl_runtime_c__String__copy(
      &(input->text), &(output->text)))
  {
    return false;
  }
  // language
  if (!rosidl_runtime_c__String__copy(
      &(input->language), &(output->language)))
  {
    return false;
  }
  // is_final
  output->is_final = input->is_final;
  // session_id
  if (!rosidl_runtime_c__String__copy(
      &(input->session_id), &(output->session_id)))
  {
    return false;
  }
  return true;
}

conversational_interfaces__msg__TextChunk *
conversational_interfaces__msg__TextChunk__create(void)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__TextChunk * msg = (conversational_interfaces__msg__TextChunk *)allocator.allocate(sizeof(conversational_interfaces__msg__TextChunk), allocator.state);
  if (!msg) {
    return NULL;
  }
  memset(msg, 0, sizeof(conversational_interfaces__msg__TextChunk));
  bool success = conversational_interfaces__msg__TextChunk__init(msg);
  if (!success) {
    allocator.deallocate(msg, allocator.state);
    return NULL;
  }
  return msg;
}

void
conversational_interfaces__msg__TextChunk__destroy(conversational_interfaces__msg__TextChunk * msg)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (msg) {
    conversational_interfaces__msg__TextChunk__fini(msg);
  }
  allocator.deallocate(msg, allocator.state);
}


bool
conversational_interfaces__msg__TextChunk__Sequence__init(conversational_interfaces__msg__TextChunk__Sequence * array, size_t size)
{
  if (!array) {
    return false;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__TextChunk * data = NULL;

  if (size) {
    data = (conversational_interfaces__msg__TextChunk *)allocator.zero_allocate(size, sizeof(conversational_interfaces__msg__TextChunk), allocator.state);
    if (!data) {
      return false;
    }
    // initialize all array elements
    size_t i;
    for (i = 0; i < size; ++i) {
      bool success = conversational_interfaces__msg__TextChunk__init(&data[i]);
      if (!success) {
        break;
      }
    }
    if (i < size) {
      // if initialization failed finalize the already initialized array elements
      for (; i > 0; --i) {
        conversational_interfaces__msg__TextChunk__fini(&data[i - 1]);
      }
      allocator.deallocate(data, allocator.state);
      return false;
    }
  }
  array->data = data;
  array->size = size;
  array->capacity = size;
  return true;
}

void
conversational_interfaces__msg__TextChunk__Sequence__fini(conversational_interfaces__msg__TextChunk__Sequence * array)
{
  if (!array) {
    return;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();

  if (array->data) {
    // ensure that data and capacity values are consistent
    assert(array->capacity > 0);
    // finalize all array elements
    for (size_t i = 0; i < array->capacity; ++i) {
      conversational_interfaces__msg__TextChunk__fini(&array->data[i]);
    }
    allocator.deallocate(array->data, allocator.state);
    array->data = NULL;
    array->size = 0;
    array->capacity = 0;
  } else {
    // ensure that data, size, and capacity values are consistent
    assert(0 == array->size);
    assert(0 == array->capacity);
  }
}

conversational_interfaces__msg__TextChunk__Sequence *
conversational_interfaces__msg__TextChunk__Sequence__create(size_t size)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__TextChunk__Sequence * array = (conversational_interfaces__msg__TextChunk__Sequence *)allocator.allocate(sizeof(conversational_interfaces__msg__TextChunk__Sequence), allocator.state);
  if (!array) {
    return NULL;
  }
  bool success = conversational_interfaces__msg__TextChunk__Sequence__init(array, size);
  if (!success) {
    allocator.deallocate(array, allocator.state);
    return NULL;
  }
  return array;
}

void
conversational_interfaces__msg__TextChunk__Sequence__destroy(conversational_interfaces__msg__TextChunk__Sequence * array)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (array) {
    conversational_interfaces__msg__TextChunk__Sequence__fini(array);
  }
  allocator.deallocate(array, allocator.state);
}

bool
conversational_interfaces__msg__TextChunk__Sequence__are_equal(const conversational_interfaces__msg__TextChunk__Sequence * lhs, const conversational_interfaces__msg__TextChunk__Sequence * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  if (lhs->size != rhs->size) {
    return false;
  }
  for (size_t i = 0; i < lhs->size; ++i) {
    if (!conversational_interfaces__msg__TextChunk__are_equal(&(lhs->data[i]), &(rhs->data[i]))) {
      return false;
    }
  }
  return true;
}

bool
conversational_interfaces__msg__TextChunk__Sequence__copy(
  const conversational_interfaces__msg__TextChunk__Sequence * input,
  conversational_interfaces__msg__TextChunk__Sequence * output)
{
  if (!input || !output) {
    return false;
  }
  if (output->capacity < input->size) {
    const size_t allocation_size =
      input->size * sizeof(conversational_interfaces__msg__TextChunk);
    rcutils_allocator_t allocator = rcutils_get_default_allocator();
    conversational_interfaces__msg__TextChunk * data =
      (conversational_interfaces__msg__TextChunk *)allocator.reallocate(
      output->data, allocation_size, allocator.state);
    if (!data) {
      return false;
    }
    // If reallocation succeeded, memory may or may not have been moved
    // to fulfill the allocation request, invalidating output->data.
    output->data = data;
    for (size_t i = output->capacity; i < input->size; ++i) {
      if (!conversational_interfaces__msg__TextChunk__init(&output->data[i])) {
        // If initialization of any new item fails, roll back
        // all previously initialized items. Existing items
        // in output are to be left unmodified.
        for (; i-- > output->capacity; ) {
          conversational_interfaces__msg__TextChunk__fini(&output->data[i]);
        }
        return false;
      }
    }
    output->capacity = input->size;
  }
  output->size = input->size;
  for (size_t i = 0; i < input->size; ++i) {
    if (!conversational_interfaces__msg__TextChunk__copy(
        &(input->data[i]), &(output->data[i])))
    {
      return false;
    }
  }
  return true;
}

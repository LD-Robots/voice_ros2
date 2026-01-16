// generated from rosidl_generator_c/resource/idl__functions.c.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice
#include "conversational_interfaces/msg/detail/transcription__functions.h"

#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "rcutils/allocator.h"


// Include directives for member types
// Member `text`
// Member `language`
#include "rosidl_runtime_c/string_functions.h"

bool
conversational_interfaces__msg__Transcription__init(conversational_interfaces__msg__Transcription * msg)
{
  if (!msg) {
    return false;
  }
  // text
  if (!rosidl_runtime_c__String__init(&msg->text)) {
    conversational_interfaces__msg__Transcription__fini(msg);
    return false;
  }
  // language
  if (!rosidl_runtime_c__String__init(&msg->language)) {
    conversational_interfaces__msg__Transcription__fini(msg);
    return false;
  }
  // confidence
  return true;
}

void
conversational_interfaces__msg__Transcription__fini(conversational_interfaces__msg__Transcription * msg)
{
  if (!msg) {
    return;
  }
  // text
  rosidl_runtime_c__String__fini(&msg->text);
  // language
  rosidl_runtime_c__String__fini(&msg->language);
  // confidence
}

bool
conversational_interfaces__msg__Transcription__are_equal(const conversational_interfaces__msg__Transcription * lhs, const conversational_interfaces__msg__Transcription * rhs)
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
  // confidence
  if (lhs->confidence != rhs->confidence) {
    return false;
  }
  return true;
}

bool
conversational_interfaces__msg__Transcription__copy(
  const conversational_interfaces__msg__Transcription * input,
  conversational_interfaces__msg__Transcription * output)
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
  // confidence
  output->confidence = input->confidence;
  return true;
}

conversational_interfaces__msg__Transcription *
conversational_interfaces__msg__Transcription__create(void)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Transcription * msg = (conversational_interfaces__msg__Transcription *)allocator.allocate(sizeof(conversational_interfaces__msg__Transcription), allocator.state);
  if (!msg) {
    return NULL;
  }
  memset(msg, 0, sizeof(conversational_interfaces__msg__Transcription));
  bool success = conversational_interfaces__msg__Transcription__init(msg);
  if (!success) {
    allocator.deallocate(msg, allocator.state);
    return NULL;
  }
  return msg;
}

void
conversational_interfaces__msg__Transcription__destroy(conversational_interfaces__msg__Transcription * msg)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (msg) {
    conversational_interfaces__msg__Transcription__fini(msg);
  }
  allocator.deallocate(msg, allocator.state);
}


bool
conversational_interfaces__msg__Transcription__Sequence__init(conversational_interfaces__msg__Transcription__Sequence * array, size_t size)
{
  if (!array) {
    return false;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Transcription * data = NULL;

  if (size) {
    data = (conversational_interfaces__msg__Transcription *)allocator.zero_allocate(size, sizeof(conversational_interfaces__msg__Transcription), allocator.state);
    if (!data) {
      return false;
    }
    // initialize all array elements
    size_t i;
    for (i = 0; i < size; ++i) {
      bool success = conversational_interfaces__msg__Transcription__init(&data[i]);
      if (!success) {
        break;
      }
    }
    if (i < size) {
      // if initialization failed finalize the already initialized array elements
      for (; i > 0; --i) {
        conversational_interfaces__msg__Transcription__fini(&data[i - 1]);
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
conversational_interfaces__msg__Transcription__Sequence__fini(conversational_interfaces__msg__Transcription__Sequence * array)
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
      conversational_interfaces__msg__Transcription__fini(&array->data[i]);
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

conversational_interfaces__msg__Transcription__Sequence *
conversational_interfaces__msg__Transcription__Sequence__create(size_t size)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Transcription__Sequence * array = (conversational_interfaces__msg__Transcription__Sequence *)allocator.allocate(sizeof(conversational_interfaces__msg__Transcription__Sequence), allocator.state);
  if (!array) {
    return NULL;
  }
  bool success = conversational_interfaces__msg__Transcription__Sequence__init(array, size);
  if (!success) {
    allocator.deallocate(array, allocator.state);
    return NULL;
  }
  return array;
}

void
conversational_interfaces__msg__Transcription__Sequence__destroy(conversational_interfaces__msg__Transcription__Sequence * array)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (array) {
    conversational_interfaces__msg__Transcription__Sequence__fini(array);
  }
  allocator.deallocate(array, allocator.state);
}

bool
conversational_interfaces__msg__Transcription__Sequence__are_equal(const conversational_interfaces__msg__Transcription__Sequence * lhs, const conversational_interfaces__msg__Transcription__Sequence * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  if (lhs->size != rhs->size) {
    return false;
  }
  for (size_t i = 0; i < lhs->size; ++i) {
    if (!conversational_interfaces__msg__Transcription__are_equal(&(lhs->data[i]), &(rhs->data[i]))) {
      return false;
    }
  }
  return true;
}

bool
conversational_interfaces__msg__Transcription__Sequence__copy(
  const conversational_interfaces__msg__Transcription__Sequence * input,
  conversational_interfaces__msg__Transcription__Sequence * output)
{
  if (!input || !output) {
    return false;
  }
  if (output->capacity < input->size) {
    const size_t allocation_size =
      input->size * sizeof(conversational_interfaces__msg__Transcription);
    rcutils_allocator_t allocator = rcutils_get_default_allocator();
    conversational_interfaces__msg__Transcription * data =
      (conversational_interfaces__msg__Transcription *)allocator.reallocate(
      output->data, allocation_size, allocator.state);
    if (!data) {
      return false;
    }
    // If reallocation succeeded, memory may or may not have been moved
    // to fulfill the allocation request, invalidating output->data.
    output->data = data;
    for (size_t i = output->capacity; i < input->size; ++i) {
      if (!conversational_interfaces__msg__Transcription__init(&output->data[i])) {
        // If initialization of any new item fails, roll back
        // all previously initialized items. Existing items
        // in output are to be left unmodified.
        for (; i-- > output->capacity; ) {
          conversational_interfaces__msg__Transcription__fini(&output->data[i]);
        }
        return false;
      }
    }
    output->capacity = input->size;
  }
  output->size = input->size;
  for (size_t i = 0; i < input->size; ++i) {
    if (!conversational_interfaces__msg__Transcription__copy(
        &(input->data[i]), &(output->data[i])))
    {
      return false;
    }
  }
  return true;
}

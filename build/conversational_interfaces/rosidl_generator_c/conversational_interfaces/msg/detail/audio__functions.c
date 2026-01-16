// generated from rosidl_generator_c/resource/idl__functions.c.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice
#include "conversational_interfaces/msg/detail/audio__functions.h"

#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "rcutils/allocator.h"


// Include directives for member types
// Member `data`
#include "rosidl_runtime_c/primitives_sequence_functions.h"

bool
conversational_interfaces__msg__Audio__init(conversational_interfaces__msg__Audio * msg)
{
  if (!msg) {
    return false;
  }
  // sample_rate
  // channels
  // data
  if (!rosidl_runtime_c__int16__Sequence__init(&msg->data, 0)) {
    conversational_interfaces__msg__Audio__fini(msg);
    return false;
  }
  return true;
}

void
conversational_interfaces__msg__Audio__fini(conversational_interfaces__msg__Audio * msg)
{
  if (!msg) {
    return;
  }
  // sample_rate
  // channels
  // data
  rosidl_runtime_c__int16__Sequence__fini(&msg->data);
}

bool
conversational_interfaces__msg__Audio__are_equal(const conversational_interfaces__msg__Audio * lhs, const conversational_interfaces__msg__Audio * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  // sample_rate
  if (lhs->sample_rate != rhs->sample_rate) {
    return false;
  }
  // channels
  if (lhs->channels != rhs->channels) {
    return false;
  }
  // data
  if (!rosidl_runtime_c__int16__Sequence__are_equal(
      &(lhs->data), &(rhs->data)))
  {
    return false;
  }
  return true;
}

bool
conversational_interfaces__msg__Audio__copy(
  const conversational_interfaces__msg__Audio * input,
  conversational_interfaces__msg__Audio * output)
{
  if (!input || !output) {
    return false;
  }
  // sample_rate
  output->sample_rate = input->sample_rate;
  // channels
  output->channels = input->channels;
  // data
  if (!rosidl_runtime_c__int16__Sequence__copy(
      &(input->data), &(output->data)))
  {
    return false;
  }
  return true;
}

conversational_interfaces__msg__Audio *
conversational_interfaces__msg__Audio__create(void)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Audio * msg = (conversational_interfaces__msg__Audio *)allocator.allocate(sizeof(conversational_interfaces__msg__Audio), allocator.state);
  if (!msg) {
    return NULL;
  }
  memset(msg, 0, sizeof(conversational_interfaces__msg__Audio));
  bool success = conversational_interfaces__msg__Audio__init(msg);
  if (!success) {
    allocator.deallocate(msg, allocator.state);
    return NULL;
  }
  return msg;
}

void
conversational_interfaces__msg__Audio__destroy(conversational_interfaces__msg__Audio * msg)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (msg) {
    conversational_interfaces__msg__Audio__fini(msg);
  }
  allocator.deallocate(msg, allocator.state);
}


bool
conversational_interfaces__msg__Audio__Sequence__init(conversational_interfaces__msg__Audio__Sequence * array, size_t size)
{
  if (!array) {
    return false;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Audio * data = NULL;

  if (size) {
    data = (conversational_interfaces__msg__Audio *)allocator.zero_allocate(size, sizeof(conversational_interfaces__msg__Audio), allocator.state);
    if (!data) {
      return false;
    }
    // initialize all array elements
    size_t i;
    for (i = 0; i < size; ++i) {
      bool success = conversational_interfaces__msg__Audio__init(&data[i]);
      if (!success) {
        break;
      }
    }
    if (i < size) {
      // if initialization failed finalize the already initialized array elements
      for (; i > 0; --i) {
        conversational_interfaces__msg__Audio__fini(&data[i - 1]);
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
conversational_interfaces__msg__Audio__Sequence__fini(conversational_interfaces__msg__Audio__Sequence * array)
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
      conversational_interfaces__msg__Audio__fini(&array->data[i]);
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

conversational_interfaces__msg__Audio__Sequence *
conversational_interfaces__msg__Audio__Sequence__create(size_t size)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  conversational_interfaces__msg__Audio__Sequence * array = (conversational_interfaces__msg__Audio__Sequence *)allocator.allocate(sizeof(conversational_interfaces__msg__Audio__Sequence), allocator.state);
  if (!array) {
    return NULL;
  }
  bool success = conversational_interfaces__msg__Audio__Sequence__init(array, size);
  if (!success) {
    allocator.deallocate(array, allocator.state);
    return NULL;
  }
  return array;
}

void
conversational_interfaces__msg__Audio__Sequence__destroy(conversational_interfaces__msg__Audio__Sequence * array)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (array) {
    conversational_interfaces__msg__Audio__Sequence__fini(array);
  }
  allocator.deallocate(array, allocator.state);
}

bool
conversational_interfaces__msg__Audio__Sequence__are_equal(const conversational_interfaces__msg__Audio__Sequence * lhs, const conversational_interfaces__msg__Audio__Sequence * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  if (lhs->size != rhs->size) {
    return false;
  }
  for (size_t i = 0; i < lhs->size; ++i) {
    if (!conversational_interfaces__msg__Audio__are_equal(&(lhs->data[i]), &(rhs->data[i]))) {
      return false;
    }
  }
  return true;
}

bool
conversational_interfaces__msg__Audio__Sequence__copy(
  const conversational_interfaces__msg__Audio__Sequence * input,
  conversational_interfaces__msg__Audio__Sequence * output)
{
  if (!input || !output) {
    return false;
  }
  if (output->capacity < input->size) {
    const size_t allocation_size =
      input->size * sizeof(conversational_interfaces__msg__Audio);
    rcutils_allocator_t allocator = rcutils_get_default_allocator();
    conversational_interfaces__msg__Audio * data =
      (conversational_interfaces__msg__Audio *)allocator.reallocate(
      output->data, allocation_size, allocator.state);
    if (!data) {
      return false;
    }
    // If reallocation succeeded, memory may or may not have been moved
    // to fulfill the allocation request, invalidating output->data.
    output->data = data;
    for (size_t i = output->capacity; i < input->size; ++i) {
      if (!conversational_interfaces__msg__Audio__init(&output->data[i])) {
        // If initialization of any new item fails, roll back
        // all previously initialized items. Existing items
        // in output are to be left unmodified.
        for (; i-- > output->capacity; ) {
          conversational_interfaces__msg__Audio__fini(&output->data[i]);
        }
        return false;
      }
    }
    output->capacity = input->size;
  }
  output->size = input->size;
  for (size_t i = 0; i < input->size; ++i) {
    if (!conversational_interfaces__msg__Audio__copy(
        &(input->data[i]), &(output->data[i])))
    {
      return false;
    }
  }
  return true;
}

// generated from rosidl_generator_cpp/resource/idl__struct.hpp.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/transcription.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__STRUCT_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__STRUCT_HPP_

#include <algorithm>
#include <array>
#include <memory>
#include <string>
#include <vector>

#include "rosidl_runtime_cpp/bounded_vector.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


#ifndef _WIN32
# define DEPRECATED__conversational_interfaces__msg__Transcription __attribute__((deprecated))
#else
# define DEPRECATED__conversational_interfaces__msg__Transcription __declspec(deprecated)
#endif

namespace conversational_interfaces
{

namespace msg
{

// message struct
template<class ContainerAllocator>
struct Transcription_
{
  using Type = Transcription_<ContainerAllocator>;

  explicit Transcription_(rosidl_runtime_cpp::MessageInitialization _init = rosidl_runtime_cpp::MessageInitialization::ALL)
  {
    if (rosidl_runtime_cpp::MessageInitialization::ALL == _init ||
      rosidl_runtime_cpp::MessageInitialization::ZERO == _init)
    {
      this->text = "";
      this->language = "";
      this->confidence = 0.0f;
    }
  }

  explicit Transcription_(const ContainerAllocator & _alloc, rosidl_runtime_cpp::MessageInitialization _init = rosidl_runtime_cpp::MessageInitialization::ALL)
  : text(_alloc),
    language(_alloc)
  {
    if (rosidl_runtime_cpp::MessageInitialization::ALL == _init ||
      rosidl_runtime_cpp::MessageInitialization::ZERO == _init)
    {
      this->text = "";
      this->language = "";
      this->confidence = 0.0f;
    }
  }

  // field types and members
  using _text_type =
    std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>>;
  _text_type text;
  using _language_type =
    std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>>;
  _language_type language;
  using _confidence_type =
    float;
  _confidence_type confidence;

  // setters for named parameter idiom
  Type & set__text(
    const std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>> & _arg)
  {
    this->text = _arg;
    return *this;
  }
  Type & set__language(
    const std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>> & _arg)
  {
    this->language = _arg;
    return *this;
  }
  Type & set__confidence(
    const float & _arg)
  {
    this->confidence = _arg;
    return *this;
  }

  // constant declarations

  // pointer types
  using RawPtr =
    conversational_interfaces::msg::Transcription_<ContainerAllocator> *;
  using ConstRawPtr =
    const conversational_interfaces::msg::Transcription_<ContainerAllocator> *;
  using SharedPtr =
    std::shared_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator>>;
  using ConstSharedPtr =
    std::shared_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator> const>;

  template<typename Deleter = std::default_delete<
      conversational_interfaces::msg::Transcription_<ContainerAllocator>>>
  using UniquePtrWithDeleter =
    std::unique_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator>, Deleter>;

  using UniquePtr = UniquePtrWithDeleter<>;

  template<typename Deleter = std::default_delete<
      conversational_interfaces::msg::Transcription_<ContainerAllocator>>>
  using ConstUniquePtrWithDeleter =
    std::unique_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator> const, Deleter>;
  using ConstUniquePtr = ConstUniquePtrWithDeleter<>;

  using WeakPtr =
    std::weak_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator>>;
  using ConstWeakPtr =
    std::weak_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator> const>;

  // pointer types similar to ROS 1, use SharedPtr / ConstSharedPtr instead
  // NOTE: Can't use 'using' here because GNU C++ can't parse attributes properly
  typedef DEPRECATED__conversational_interfaces__msg__Transcription
    std::shared_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator>>
    Ptr;
  typedef DEPRECATED__conversational_interfaces__msg__Transcription
    std::shared_ptr<conversational_interfaces::msg::Transcription_<ContainerAllocator> const>
    ConstPtr;

  // comparison operators
  bool operator==(const Transcription_ & other) const
  {
    if (this->text != other.text) {
      return false;
    }
    if (this->language != other.language) {
      return false;
    }
    if (this->confidence != other.confidence) {
      return false;
    }
    return true;
  }
  bool operator!=(const Transcription_ & other) const
  {
    return !this->operator==(other);
  }
};  // struct Transcription_

// alias to use template instance with default allocator
using Transcription =
  conversational_interfaces::msg::Transcription_<std::allocator<void>>;

// constant definitions

}  // namespace msg

}  // namespace conversational_interfaces

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__STRUCT_HPP_

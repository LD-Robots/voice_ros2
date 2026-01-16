// generated from rosidl_generator_cpp/resource/idl__struct.hpp.em
// with input from conversational_interfaces:msg/TextChunk.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/text_chunk.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_HPP_

#include <algorithm>
#include <array>
#include <memory>
#include <string>
#include <vector>

#include "rosidl_runtime_cpp/bounded_vector.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


#ifndef _WIN32
# define DEPRECATED__conversational_interfaces__msg__TextChunk __attribute__((deprecated))
#else
# define DEPRECATED__conversational_interfaces__msg__TextChunk __declspec(deprecated)
#endif

namespace conversational_interfaces
{

namespace msg
{

// message struct
template<class ContainerAllocator>
struct TextChunk_
{
  using Type = TextChunk_<ContainerAllocator>;

  explicit TextChunk_(rosidl_runtime_cpp::MessageInitialization _init = rosidl_runtime_cpp::MessageInitialization::ALL)
  {
    if (rosidl_runtime_cpp::MessageInitialization::ALL == _init ||
      rosidl_runtime_cpp::MessageInitialization::ZERO == _init)
    {
      this->text = "";
      this->language = "";
      this->is_final = false;
      this->session_id = "";
    }
  }

  explicit TextChunk_(const ContainerAllocator & _alloc, rosidl_runtime_cpp::MessageInitialization _init = rosidl_runtime_cpp::MessageInitialization::ALL)
  : text(_alloc),
    language(_alloc),
    session_id(_alloc)
  {
    if (rosidl_runtime_cpp::MessageInitialization::ALL == _init ||
      rosidl_runtime_cpp::MessageInitialization::ZERO == _init)
    {
      this->text = "";
      this->language = "";
      this->is_final = false;
      this->session_id = "";
    }
  }

  // field types and members
  using _text_type =
    std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>>;
  _text_type text;
  using _language_type =
    std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>>;
  _language_type language;
  using _is_final_type =
    bool;
  _is_final_type is_final;
  using _session_id_type =
    std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>>;
  _session_id_type session_id;

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
  Type & set__is_final(
    const bool & _arg)
  {
    this->is_final = _arg;
    return *this;
  }
  Type & set__session_id(
    const std::basic_string<char, std::char_traits<char>, typename std::allocator_traits<ContainerAllocator>::template rebind_alloc<char>> & _arg)
  {
    this->session_id = _arg;
    return *this;
  }

  // constant declarations

  // pointer types
  using RawPtr =
    conversational_interfaces::msg::TextChunk_<ContainerAllocator> *;
  using ConstRawPtr =
    const conversational_interfaces::msg::TextChunk_<ContainerAllocator> *;
  using SharedPtr =
    std::shared_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator>>;
  using ConstSharedPtr =
    std::shared_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator> const>;

  template<typename Deleter = std::default_delete<
      conversational_interfaces::msg::TextChunk_<ContainerAllocator>>>
  using UniquePtrWithDeleter =
    std::unique_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator>, Deleter>;

  using UniquePtr = UniquePtrWithDeleter<>;

  template<typename Deleter = std::default_delete<
      conversational_interfaces::msg::TextChunk_<ContainerAllocator>>>
  using ConstUniquePtrWithDeleter =
    std::unique_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator> const, Deleter>;
  using ConstUniquePtr = ConstUniquePtrWithDeleter<>;

  using WeakPtr =
    std::weak_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator>>;
  using ConstWeakPtr =
    std::weak_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator> const>;

  // pointer types similar to ROS 1, use SharedPtr / ConstSharedPtr instead
  // NOTE: Can't use 'using' here because GNU C++ can't parse attributes properly
  typedef DEPRECATED__conversational_interfaces__msg__TextChunk
    std::shared_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator>>
    Ptr;
  typedef DEPRECATED__conversational_interfaces__msg__TextChunk
    std::shared_ptr<conversational_interfaces::msg::TextChunk_<ContainerAllocator> const>
    ConstPtr;

  // comparison operators
  bool operator==(const TextChunk_ & other) const
  {
    if (this->text != other.text) {
      return false;
    }
    if (this->language != other.language) {
      return false;
    }
    if (this->is_final != other.is_final) {
      return false;
    }
    if (this->session_id != other.session_id) {
      return false;
    }
    return true;
  }
  bool operator!=(const TextChunk_ & other) const
  {
    return !this->operator==(other);
  }
};  // struct TextChunk_

// alias to use template instance with default allocator
using TextChunk =
  conversational_interfaces::msg::TextChunk_<std::allocator<void>>;

// constant definitions

}  // namespace msg

}  // namespace conversational_interfaces

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__STRUCT_HPP_

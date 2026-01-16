// generated from rosidl_generator_cpp/resource/idl__traits.hpp.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/audio.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__TRAITS_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__TRAITS_HPP_

#include <stdint.h>

#include <sstream>
#include <string>
#include <type_traits>

#include "conversational_interfaces/msg/detail/audio__struct.hpp"
#include "rosidl_runtime_cpp/traits.hpp"

namespace conversational_interfaces
{

namespace msg
{

inline void to_flow_style_yaml(
  const Audio & msg,
  std::ostream & out)
{
  out << "{";
  // member: sample_rate
  {
    out << "sample_rate: ";
    rosidl_generator_traits::value_to_yaml(msg.sample_rate, out);
    out << ", ";
  }

  // member: channels
  {
    out << "channels: ";
    rosidl_generator_traits::value_to_yaml(msg.channels, out);
    out << ", ";
  }

  // member: data
  {
    if (msg.data.size() == 0) {
      out << "data: []";
    } else {
      out << "data: [";
      size_t pending_items = msg.data.size();
      for (auto item : msg.data) {
        rosidl_generator_traits::value_to_yaml(item, out);
        if (--pending_items > 0) {
          out << ", ";
        }
      }
      out << "]";
    }
  }
  out << "}";
}  // NOLINT(readability/fn_size)

inline void to_block_style_yaml(
  const Audio & msg,
  std::ostream & out, size_t indentation = 0)
{
  // member: sample_rate
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "sample_rate: ";
    rosidl_generator_traits::value_to_yaml(msg.sample_rate, out);
    out << "\n";
  }

  // member: channels
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "channels: ";
    rosidl_generator_traits::value_to_yaml(msg.channels, out);
    out << "\n";
  }

  // member: data
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    if (msg.data.size() == 0) {
      out << "data: []\n";
    } else {
      out << "data:\n";
      for (auto item : msg.data) {
        if (indentation > 0) {
          out << std::string(indentation, ' ');
        }
        out << "- ";
        rosidl_generator_traits::value_to_yaml(item, out);
        out << "\n";
      }
    }
  }
}  // NOLINT(readability/fn_size)

inline std::string to_yaml(const Audio & msg, bool use_flow_style = false)
{
  std::ostringstream out;
  if (use_flow_style) {
    to_flow_style_yaml(msg, out);
  } else {
    to_block_style_yaml(msg, out);
  }
  return out.str();
}

}  // namespace msg

}  // namespace conversational_interfaces

namespace rosidl_generator_traits
{

[[deprecated("use conversational_interfaces::msg::to_block_style_yaml() instead")]]
inline void to_yaml(
  const conversational_interfaces::msg::Audio & msg,
  std::ostream & out, size_t indentation = 0)
{
  conversational_interfaces::msg::to_block_style_yaml(msg, out, indentation);
}

[[deprecated("use conversational_interfaces::msg::to_yaml() instead")]]
inline std::string to_yaml(const conversational_interfaces::msg::Audio & msg)
{
  return conversational_interfaces::msg::to_yaml(msg);
}

template<>
inline const char * data_type<conversational_interfaces::msg::Audio>()
{
  return "conversational_interfaces::msg::Audio";
}

template<>
inline const char * name<conversational_interfaces::msg::Audio>()
{
  return "conversational_interfaces/msg/Audio";
}

template<>
struct has_fixed_size<conversational_interfaces::msg::Audio>
  : std::integral_constant<bool, false> {};

template<>
struct has_bounded_size<conversational_interfaces::msg::Audio>
  : std::integral_constant<bool, false> {};

template<>
struct is_message<conversational_interfaces::msg::Audio>
  : std::true_type {};

}  // namespace rosidl_generator_traits

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__TRAITS_HPP_

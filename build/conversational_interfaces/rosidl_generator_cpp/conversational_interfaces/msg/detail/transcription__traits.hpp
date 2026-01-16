// generated from rosidl_generator_cpp/resource/idl__traits.hpp.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/transcription.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__TRAITS_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__TRAITS_HPP_

#include <stdint.h>

#include <sstream>
#include <string>
#include <type_traits>

#include "conversational_interfaces/msg/detail/transcription__struct.hpp"
#include "rosidl_runtime_cpp/traits.hpp"

namespace conversational_interfaces
{

namespace msg
{

inline void to_flow_style_yaml(
  const Transcription & msg,
  std::ostream & out)
{
  out << "{";
  // member: text
  {
    out << "text: ";
    rosidl_generator_traits::value_to_yaml(msg.text, out);
    out << ", ";
  }

  // member: language
  {
    out << "language: ";
    rosidl_generator_traits::value_to_yaml(msg.language, out);
    out << ", ";
  }

  // member: confidence
  {
    out << "confidence: ";
    rosidl_generator_traits::value_to_yaml(msg.confidence, out);
  }
  out << "}";
}  // NOLINT(readability/fn_size)

inline void to_block_style_yaml(
  const Transcription & msg,
  std::ostream & out, size_t indentation = 0)
{
  // member: text
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "text: ";
    rosidl_generator_traits::value_to_yaml(msg.text, out);
    out << "\n";
  }

  // member: language
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "language: ";
    rosidl_generator_traits::value_to_yaml(msg.language, out);
    out << "\n";
  }

  // member: confidence
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "confidence: ";
    rosidl_generator_traits::value_to_yaml(msg.confidence, out);
    out << "\n";
  }
}  // NOLINT(readability/fn_size)

inline std::string to_yaml(const Transcription & msg, bool use_flow_style = false)
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
  const conversational_interfaces::msg::Transcription & msg,
  std::ostream & out, size_t indentation = 0)
{
  conversational_interfaces::msg::to_block_style_yaml(msg, out, indentation);
}

[[deprecated("use conversational_interfaces::msg::to_yaml() instead")]]
inline std::string to_yaml(const conversational_interfaces::msg::Transcription & msg)
{
  return conversational_interfaces::msg::to_yaml(msg);
}

template<>
inline const char * data_type<conversational_interfaces::msg::Transcription>()
{
  return "conversational_interfaces::msg::Transcription";
}

template<>
inline const char * name<conversational_interfaces::msg::Transcription>()
{
  return "conversational_interfaces/msg/Transcription";
}

template<>
struct has_fixed_size<conversational_interfaces::msg::Transcription>
  : std::integral_constant<bool, false> {};

template<>
struct has_bounded_size<conversational_interfaces::msg::Transcription>
  : std::integral_constant<bool, false> {};

template<>
struct is_message<conversational_interfaces::msg::Transcription>
  : std::true_type {};

}  // namespace rosidl_generator_traits

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__TRAITS_HPP_

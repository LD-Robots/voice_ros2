// generated from rosidl_generator_cpp/resource/idl__builder.hpp.em
// with input from conversational_interfaces:msg/Transcription.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/transcription.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__BUILDER_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__BUILDER_HPP_

#include <algorithm>
#include <utility>

#include "conversational_interfaces/msg/detail/transcription__struct.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


namespace conversational_interfaces
{

namespace msg
{

namespace builder
{

class Init_Transcription_confidence
{
public:
  explicit Init_Transcription_confidence(::conversational_interfaces::msg::Transcription & msg)
  : msg_(msg)
  {}
  ::conversational_interfaces::msg::Transcription confidence(::conversational_interfaces::msg::Transcription::_confidence_type arg)
  {
    msg_.confidence = std::move(arg);
    return std::move(msg_);
  }

private:
  ::conversational_interfaces::msg::Transcription msg_;
};

class Init_Transcription_language
{
public:
  explicit Init_Transcription_language(::conversational_interfaces::msg::Transcription & msg)
  : msg_(msg)
  {}
  Init_Transcription_confidence language(::conversational_interfaces::msg::Transcription::_language_type arg)
  {
    msg_.language = std::move(arg);
    return Init_Transcription_confidence(msg_);
  }

private:
  ::conversational_interfaces::msg::Transcription msg_;
};

class Init_Transcription_text
{
public:
  Init_Transcription_text()
  : msg_(::rosidl_runtime_cpp::MessageInitialization::SKIP)
  {}
  Init_Transcription_language text(::conversational_interfaces::msg::Transcription::_text_type arg)
  {
    msg_.text = std::move(arg);
    return Init_Transcription_language(msg_);
  }

private:
  ::conversational_interfaces::msg::Transcription msg_;
};

}  // namespace builder

}  // namespace msg

template<typename MessageType>
auto build();

template<>
inline
auto build<::conversational_interfaces::msg::Transcription>()
{
  return conversational_interfaces::msg::builder::Init_Transcription_text();
}

}  // namespace conversational_interfaces

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TRANSCRIPTION__BUILDER_HPP_

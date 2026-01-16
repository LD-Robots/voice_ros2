// generated from rosidl_generator_cpp/resource/idl__builder.hpp.em
// with input from conversational_interfaces:msg/TextChunk.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/text_chunk.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__BUILDER_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__BUILDER_HPP_

#include <algorithm>
#include <utility>

#include "conversational_interfaces/msg/detail/text_chunk__struct.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


namespace conversational_interfaces
{

namespace msg
{

namespace builder
{

class Init_TextChunk_session_id
{
public:
  explicit Init_TextChunk_session_id(::conversational_interfaces::msg::TextChunk & msg)
  : msg_(msg)
  {}
  ::conversational_interfaces::msg::TextChunk session_id(::conversational_interfaces::msg::TextChunk::_session_id_type arg)
  {
    msg_.session_id = std::move(arg);
    return std::move(msg_);
  }

private:
  ::conversational_interfaces::msg::TextChunk msg_;
};

class Init_TextChunk_is_final
{
public:
  explicit Init_TextChunk_is_final(::conversational_interfaces::msg::TextChunk & msg)
  : msg_(msg)
  {}
  Init_TextChunk_session_id is_final(::conversational_interfaces::msg::TextChunk::_is_final_type arg)
  {
    msg_.is_final = std::move(arg);
    return Init_TextChunk_session_id(msg_);
  }

private:
  ::conversational_interfaces::msg::TextChunk msg_;
};

class Init_TextChunk_language
{
public:
  explicit Init_TextChunk_language(::conversational_interfaces::msg::TextChunk & msg)
  : msg_(msg)
  {}
  Init_TextChunk_is_final language(::conversational_interfaces::msg::TextChunk::_language_type arg)
  {
    msg_.language = std::move(arg);
    return Init_TextChunk_is_final(msg_);
  }

private:
  ::conversational_interfaces::msg::TextChunk msg_;
};

class Init_TextChunk_text
{
public:
  Init_TextChunk_text()
  : msg_(::rosidl_runtime_cpp::MessageInitialization::SKIP)
  {}
  Init_TextChunk_language text(::conversational_interfaces::msg::TextChunk::_text_type arg)
  {
    msg_.text = std::move(arg);
    return Init_TextChunk_language(msg_);
  }

private:
  ::conversational_interfaces::msg::TextChunk msg_;
};

}  // namespace builder

}  // namespace msg

template<typename MessageType>
auto build();

template<>
inline
auto build<::conversational_interfaces::msg::TextChunk>()
{
  return conversational_interfaces::msg::builder::Init_TextChunk_text();
}

}  // namespace conversational_interfaces

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__TEXT_CHUNK__BUILDER_HPP_

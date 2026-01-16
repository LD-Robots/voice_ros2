// generated from rosidl_generator_cpp/resource/idl__builder.hpp.em
// with input from conversational_interfaces:msg/Audio.idl
// generated code does not contain a copyright notice

// IWYU pragma: private, include "conversational_interfaces/msg/audio.hpp"


#ifndef CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__BUILDER_HPP_
#define CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__BUILDER_HPP_

#include <algorithm>
#include <utility>

#include "conversational_interfaces/msg/detail/audio__struct.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


namespace conversational_interfaces
{

namespace msg
{

namespace builder
{

class Init_Audio_data
{
public:
  explicit Init_Audio_data(::conversational_interfaces::msg::Audio & msg)
  : msg_(msg)
  {}
  ::conversational_interfaces::msg::Audio data(::conversational_interfaces::msg::Audio::_data_type arg)
  {
    msg_.data = std::move(arg);
    return std::move(msg_);
  }

private:
  ::conversational_interfaces::msg::Audio msg_;
};

class Init_Audio_channels
{
public:
  explicit Init_Audio_channels(::conversational_interfaces::msg::Audio & msg)
  : msg_(msg)
  {}
  Init_Audio_data channels(::conversational_interfaces::msg::Audio::_channels_type arg)
  {
    msg_.channels = std::move(arg);
    return Init_Audio_data(msg_);
  }

private:
  ::conversational_interfaces::msg::Audio msg_;
};

class Init_Audio_sample_rate
{
public:
  Init_Audio_sample_rate()
  : msg_(::rosidl_runtime_cpp::MessageInitialization::SKIP)
  {}
  Init_Audio_channels sample_rate(::conversational_interfaces::msg::Audio::_sample_rate_type arg)
  {
    msg_.sample_rate = std::move(arg);
    return Init_Audio_channels(msg_);
  }

private:
  ::conversational_interfaces::msg::Audio msg_;
};

}  // namespace builder

}  // namespace msg

template<typename MessageType>
auto build();

template<>
inline
auto build<::conversational_interfaces::msg::Audio>()
{
  return conversational_interfaces::msg::builder::Init_Audio_sample_rate();
}

}  // namespace conversational_interfaces

#endif  // CONVERSATIONAL_INTERFACES__MSG__DETAIL__AUDIO__BUILDER_HPP_

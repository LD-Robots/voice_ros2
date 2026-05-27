#!/usr/bin/env python3
import asyncio
import os
import sys
import threading
import numpy as np
import soxr

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32
from conversational_interfaces.msg import Audio, Transcription

# --- Pipecat Imports ---
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, StartFrame, EndFrame, CancelFrame, InterruptionFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService, OpenAIRealtimeLLMSettings
from pipecat.services.openai.realtime.events import (
    SessionProperties, AudioConfiguration, AudioInput, AudioOutput, 
    TurnDetection, InputAudioTranscription, PCMAudioFormat
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.frames.frames import TranscriptionFrame, LLMMessagesAppendFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from .prompt_config import load_prompt_defaults
from .language_utils import ConversationLanguageTracker

class ROSAudioInputTransport(BaseInputTransport):
    def __init__(self, params: TransportParams):
        super().__init__(params)
        self._is_running = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._is_running = True
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._is_running = False

    async def push_ros_audio(self, pcm_data: bytes):
        """Called by ROS2 callback to push audio into Pipecat pipeline"""
        if not self._is_running:
            return

        # Resample from 16kHz to 24kHz for OpenAI
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        resampled = soxr.resample(audio_array, 16000, 24000, 'HQ').astype(np.int16)
        frame = InputAudioRawFrame(
            audio=resampled.tobytes(),
            sample_rate=24000,
            num_channels=1
        )
        await self.push_frame(frame)

class ROSAudioOutputTransport(BaseOutputTransport):
    def __init__(self, params: TransportParams, ros_node):
        super().__init__(params)
        self.ros_node = ros_node

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, OutputAudioRawFrame):
            # Resample from 24kHz to 16kHz for ROS playback
            audio_array = np.frombuffer(frame.audio, dtype=np.int16)
            resampled = soxr.resample(audio_array, 24000, 16000, 'HQ').astype(np.int16)
            
            # Publish to ROS
            msg = Audio()
            msg.data = resampled.tolist()
            self.ros_node.audio_pub.publish(msg)
            
        elif isinstance(frame, TTSStartedFrame):
            msg = Bool()
            msg.data = True
            self.ros_node.is_speaking_pub.publish(msg)
            self.ros_node.is_speaking = True
            
        elif isinstance(frame, TTSStoppedFrame):
            msg = Bool()
            msg.data = False
            self.ros_node.is_speaking_pub.publish(msg)
            self.ros_node.is_speaking = False
            
        elif isinstance(frame, (CancelFrame, InterruptionFrame)):
            # Immediately stop the playback buffer
            msg = Bool()
            msg.data = True
            self.ros_node.tts_stop_pub.publish(msg)
            
            # Also reset speaking state
            speak_msg = Bool()
            speak_msg.data = False
            self.ros_node.is_speaking_pub.publish(speak_msg)
            self.ros_node.is_speaking = False

class ROSPipecatTransport(BaseTransport):
    def __init__(self, ros_node, params=TransportParams(audio_out_sample_rate=24000)):
        super().__init__()
        self._input = ROSAudioInputTransport(params)
        self._output = ROSAudioOutputTransport(params, ros_node)

    def input(self) -> BaseInputTransport:
        return self._input

    def output(self) -> BaseOutputTransport:
        return self._output


from pathlib import Path

def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None

class LanguageTrackerProcessor(FrameProcessor):
    def __init__(self, ros_node):
        super().__init__()
        self.ros_node = ros_node
        self.language_tracker = ConversationLanguageTracker(switch_hits_required=2)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        
        # When user finishes speaking, OpenAI sends the transcription frame
        if isinstance(frame, TranscriptionFrame): 
            text = frame.text.strip()
            if text:
                active_lang = self.language_tracker.observe(text, preferred_language='')
                self.ros_node.get_logger().info(f'User said: "{text}" (Detected lang: {active_lang})')
                
                # Publish the transcription to ROS2 so other nodes (like backend_manager) can hear it
                transcription_msg = Transcription()
                transcription_msg.text = text
                transcription_msg.language = active_lang
                transcription_msg.confidence = 1.0
                self.ros_node.transcription_pub.publish(transcription_msg)
                
                # Append a system message to guide the LLM's language
                await self.ros_node._update_llm_instructions_async(active_lang=active_lang)

class PipecatAudioNode(Node):
    def __init__(self, loop):
        super().__init__('pipecat_audio_node')
        self.loop = loop

        try:
            from dotenv import load_dotenv
            workspace_root = _find_workspace_root()
            if workspace_root:
                env_path = workspace_root / '.env'
                if env_path.exists():
                    load_dotenv(dotenv_path=env_path)
        except ImportError:
            pass

        # --- Parameters ---
        self.declare_parameter('model', 'gpt-4o-mini-realtime-preview')
        self.declare_parameter('voice', 'cedar')
        self.declare_parameter('api_sample_rate', 24000)
        self.declare_parameter('input_transcription_enabled', True)
        self.declare_parameter('input_transcription_model', 'gpt-4o-mini-transcribe')
        self.declare_parameter('vad_threshold', 0.90)
        self.declare_parameter('vad_prefix_padding_ms', 400)
        self.declare_parameter('vad_silence_duration_ms', 550)
        self.declare_parameter(
            'instructions',
            str(load_prompt_defaults().get('realtime_instructions', "Ești un asistent util."))
        )
        
        self.model = str(self.get_parameter('model').value)
        self.voice = str(self.get_parameter('voice').value)
        self.api_sample_rate = int(self.get_parameter('api_sample_rate').value)
        self.input_transcription_enabled = bool(self.get_parameter('input_transcription_enabled').value)
        self.input_transcription_model = str(self.get_parameter('input_transcription_model').value)
        self.vad_threshold = float(self.get_parameter('vad_threshold').value)
        self.vad_prefix_padding_ms = int(self.get_parameter('vad_prefix_padding_ms').value)
        self.vad_silence_duration_ms = int(self.get_parameter('vad_silence_duration_ms').value)
        self.instructions = str(self.get_parameter('instructions').value)

        # ROS2 Setup
        self.audio_sub = self.create_subscription(Audio, '/audio_clean', self.audio_callback, 10)
        self.audio_pub = self.create_publisher(Audio, '/audio_out', 10)
        
        self.session_sub = self.create_subscription(Bool, '/session_active', self.session_callback, 10)
        self.tts_stop_pub = self.create_publisher(Bool, '/stop_playback', 10)
        self.is_speaking_pub = self.create_publisher(Bool, '/is_speaking', 10)
        self.barge_in_sub = self.create_subscription(Bool, '/barge_in', self.barge_in_callback, 10)
        self.tts_stop_sub = self.create_subscription(Bool, '/tts_stop', self.barge_in_callback, 10)
        
        self.transcription_pub = self.create_publisher(Transcription, '/transcription', 10)

        # Pipecat Setup
        self.transport = ROSPipecatTransport(self)
        
        self.doa_sub = self.create_subscription(Int32, '/doa_angle', self.doa_callback, 10)
        
        # LLM service will be created per-session
        
        self.runner = PipelineRunner()
        self.task = None
        self.is_speaking = False
        self.current_lang = None
        self.current_doa = None

    def doa_callback(self, msg: Int32):
        # Only update if the session is active
        if hasattr(self, 'task') and self.task and self.llm_service:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._update_llm_instructions_async(doa=msg.data),
                self.loop
            )
        else:
            self.current_doa = msg.data



    def _get_current_instructions(self):
        # Base instructions
        new_instructions = self.instructions
        new_instructions += "\n[MULTI-PARTY RULES]: You are a robotic assistant in a crowded room. You constantly receive updates about the DOA (Direction of Arrival) angle of the sound. If the angle jumps back and forth rapidly, or if you realize from the context that two people at different angles are talking to EACH OTHER instead of to you, you MUST immediately call the ignore_background_chatter() function and stay completely silent. Only reply verbally if someone addresses you directly."
        
        # Language context
        if self.current_lang == 'ro':
            new_instructions += " Current conversation language is Romanian. Keep speaking Romanian unless the user clearly asks to switch language."
        elif self.current_lang == 'en':
            new_instructions += " Current conversation language is English. Keep speaking English unless the user clearly asks to switch language."
            
        # DOA context
        if self.current_doa is not None:
            new_instructions += f"\n[SYSTEM CONTEXT]: The user is speaking from an angle of {self.current_doa} degrees."
            
        return new_instructions

    async def _update_llm_instructions_async(self, active_lang=None, doa=None):
        if active_lang is not None:
            self.current_lang = active_lang
        if doa is not None:
            self.current_doa = doa

        new_instructions = self._get_current_instructions()
            
        if self.llm_service:
            self.llm_service._settings.session_properties.instructions = new_instructions
            await self.llm_service._send_session_update()
            self.get_logger().info(f'Updated LLM instructions (Lang: {self.current_lang}, DOA: {self.current_doa})')

    async def ignore_background_chatter_callback(self, function_name, tool_call_id, args, llm, context, result_callback):
        self.get_logger().info("🤫 LLM triggered ignore_background_chatter! Silencing response.")
        if result_callback:
            await result_callback({"status": "ignored_successfully"})

    def audio_callback(self, msg: Audio):
        if self.is_speaking:
            # Mute the mic when the robot is speaking so OpenAI ServerVAD 
            # doesn't trigger on its own echo.
            return
            
        # Push to Pipecat asynchronously from the ROS thread
        pcm_data = np.array(msg.data, dtype=np.int16).tobytes()
        asyncio.run_coroutine_threadsafe(
            self.transport._input.push_ros_audio(pcm_data),
            self.loop
        )

    def barge_in_callback(self, msg: Bool):
        if msg.data and hasattr(self, 'task') and self.task:
            self.get_logger().info('Barge-in signal received! Forcing Pipecat CancelFrame...')
            asyncio.run_coroutine_threadsafe(
                self.task.queue_frame(CancelFrame()),
                self.loop
            )

    def session_callback(self, msg: Bool):
        if msg.data and self.task is None:
            self.get_logger().info("Starting Pipecat pipeline...")
            
            turn_detection = TurnDetection(
                type='server_vad',
                threshold=self.vad_threshold,
                prefix_padding_ms=self.vad_prefix_padding_ms,
                silence_duration_ms=self.vad_silence_duration_ms
            )

            session_properties = SessionProperties(
                type='realtime',
                output_modalities=['audio'],
                instructions=self._get_current_instructions(),
                tools=[
                    {
                        "type": "function",
                        "name": "ignore_background_chatter",
                        "description": "Call this function IMMEDIATELY when you detect that the users are talking to each other instead of talking to you, OR if the DOA angle is jumping back and forth indicating background chatter. When you call this, you MUST NOT generate any verbal response.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                ],
                audio=AudioConfiguration(
                    input=AudioInput(
                        format=PCMAudioFormat(type='audio/pcm', rate=self.api_sample_rate),
                        turn_detection=turn_detection,
                        transcription=InputAudioTranscription(model=self.input_transcription_model) if self.input_transcription_enabled else None
                    ),
                    output=AudioOutput(
                        format=PCMAudioFormat(type='audio/pcm', rate=self.api_sample_rate),
                        voice=self.voice
                    )
                )
            )

            self.llm_service = OpenAIRealtimeLLMService(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                settings=OpenAIRealtimeLLMSettings(
                    model=self.model,
                    session_properties=session_properties
                )
            )
            
            self.llm_service.register_function(
                "ignore_background_chatter",
                self.ignore_background_chatter_callback
            )
            
            self.lang_processor = LanguageTrackerProcessor(self)
            
            pipeline = Pipeline([
                self.transport.input(),
                self.lang_processor,
                self.llm_service,
                self.transport.output(),
            ])
            self.task = PipelineTask(pipeline)
            asyncio.run_coroutine_threadsafe(self.runner.run(self.task), self.loop)
        elif not msg.data and self.task is not None:
            self.get_logger().info("Stopping Pipecat pipeline...")
            asyncio.run_coroutine_threadsafe(
                self.task.queue_frame(EndFrame()),
                self.loop
            )
            self.task = None

def run_ros(node):
    import rclpy.executors
    try:
        rclpy.spin(node)
    except rclpy.executors.ExternalShutdownException:
        pass
    finally:
        node.destroy_node()

async def main_async():
    rclpy.init()
    loop = asyncio.get_running_loop()
    
    node = PipecatAudioNode(loop)
    
    # Run ROS2 spin in a separate thread so asyncio event loop is not blocked
    ros_thread = threading.Thread(target=run_ros, args=(node,), daemon=True)
    ros_thread.start()

    # Keep asyncio loop alive
    try:
        while rclpy.ok():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()

def main():
    asyncio.run(main_async())

if __name__ == '__main__':
    main()

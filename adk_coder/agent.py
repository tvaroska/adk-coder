import asyncio
import os
from pathlib import Path
from typing import Optional, override, AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from google.genai import types

from acp import Client, ClientSideConnection, PROTOCOL_VERSION, RequestError, text_block
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    FileSystemCapability,
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    ReadTextFileRequest,
    ReadTextFileResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    WriteTextFileRequest,
    WriteTextFileResponse,
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallStart,
    ToolCallProgress,
    TextContentBlock,
)

# Follow-up prompts sent after processing user message
DOCKERFILE_PROMPT = "Create or update apropriate Dockerfile"
DOCUMENTATION_PROMPT = "Update GEMINI.md with informations in this session"


class GeminiClient(Client):
    """Auto-approving client for Gemini CLI integration."""

    def __init__(self, event_queue: asyncio.Queue):
        self.event_queue = event_queue

    async def requestPermission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Auto-approve all permissions."""
        try:
            # Find first allow option
            for option in params.options:
                if option.kind in {"allow_once", "allow_always"}:
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(optionId=option.optionId, outcome="selected")
                    )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        except Exception:
            # Silently ignore all exceptions
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def sessionUpdate(self, params: SessionNotification) -> None:
        """Queue session updates as events."""
        try:
            await self.event_queue.put(params.update)
        except Exception:
            # Silently ignore all exceptions
            pass

    async def writeTextFile(
        self, params: WriteTextFileRequest
    ) -> WriteTextFileResponse:
        """Write text to a file."""
        try:
            path = Path(params.path)
            if not path.is_absolute():
                raise RequestError.invalid_params(
                    {"path": params.path, "reason": "path must be absolute"}
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content)
            return WriteTextFileResponse()
        except Exception:
            # Silently ignore all exceptions
            return WriteTextFileResponse()

    async def readTextFile(
        self, params: ReadTextFileRequest
    ) -> ReadTextFileResponse:
        """Read text from a file."""
        try:
            path = Path(params.path)
            if not path.is_absolute():
                raise RequestError.invalid_params(
                    {"path": params.path, "reason": "path must be absolute"}
                )
            if not path.exists():
                # Return proper error code for non-existent files
                raise RequestError(
                    code=-32602,  # Invalid params
                    message=f"File does not exist: {params.path}",
                    data={"path": params.path, "reason": "file does not exist"}
                )
            text = path.read_text()
            return ReadTextFileResponse(content=text)
        except Exception:
            # Silently ignore all exceptions
            return ReadTextFileResponse(content="")


class CodingAgent(BaseAgent):

    root_dir: str
    docker_repo: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._gemini_proc = None
        self._gemini_conn = None

    async def _ensure_gemini_client(self):
        """Create Gemini CLI client if it doesn't exist."""
        if self._gemini_proc is None:
            # Start Gemini CLI process
            self._gemini_proc = await asyncio.create_subprocess_exec(
                "gemini",
                "--experimental-acp",
                "-y",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=10 * 1024 * 1024,  # 10MB buffer to handle large outputs
            )

            # Create event queue for session updates
            self._event_queue = asyncio.Queue()

            # Create connection
            client = GeminiClient(self._event_queue)
            self._gemini_conn = ClientSideConnection(
                lambda _: client, self._gemini_proc.stdin, self._gemini_proc.stdout
            )

            # Initialize
            await self._gemini_conn.initialize(
                InitializeRequest(
                    protocolVersion=PROTOCOL_VERSION,
                    clientCapabilities=ClientCapabilities(
                        fs=FileSystemCapability(readTextFile=True, writeTextFile=True)
                    ),
                )
            )

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
        ) -> AsyncGenerator[Event, None]:

        # Create Gemini CLI client if it doesn't exist
        await self._ensure_gemini_client()

        # Combine root_dir with directory from state
        directory = ctx.session.state.get('directory', '')
        dir_path = os.path.join(self.root_dir, directory)

        user_request = ctx.session.events[-1].content.parts[0].text

        if not os.path.exists(dir_path):
            # Create empty dir for now, create it from template in the future
            os.makedirs(dir_path, exist_ok=True)

        # Create Gemini CLI session with dir_path as working directory
        session = await self._gemini_conn.newSession(
            NewSessionRequest(cwd=dir_path, mcpServers=[])
        )

        # Send user request to Gemini CLI in background
        prompt_task = asyncio.create_task(
            self._gemini_conn.prompt(
                PromptRequest(
                    sessionId=session.sessionId,
                    prompt=[text_block(user_request)],
                )
            )
        )

        # Yield events from Gemini CLI session updates
        try:
            while True:
                # Wait for either an event or the prompt to complete
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(self._event_queue.get()),
                        prompt_task
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Check if prompt completed
                if prompt_task in done:
                    # Drain any remaining events
                    while not self._event_queue.empty():
                        update = await self._event_queue.get()
                        event_text = self._format_update(update)
                        if event_text:
                            yield Event(
                                author='coding',
                                content=types.Content(parts=[types.Part(text=event_text)])
                            )

                    # Send follow-up messages for Dockerfile and documentation
                    dockerfile_task = asyncio.create_task(
                        self._gemini_conn.prompt(
                            PromptRequest(
                                sessionId=session.sessionId,
                                prompt=[text_block(DOCKERFILE_PROMPT)],
                            )
                        )
                    )

                    # Wait for Dockerfile task and yield its events
                    while True:
                        if dockerfile_task.done():
                            while not self._event_queue.empty():
                                update = await self._event_queue.get()
                                event_text = self._format_update(update)
                                if event_text:
                                    yield Event(
                                        author='coding',
                                        content=types.Content(parts=[types.Part(text=event_text)])
                                    )
                            break

                        try:
                            update = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                            event_text = self._format_update(update)
                            if event_text:
                                yield Event(
                                    author='coding',
                                    content=types.Content(parts=[types.Part(text=event_text)])
                                )
                        except asyncio.TimeoutError:
                            continue

                    # Send documentation update message
                    docs_task = asyncio.create_task(
                        self._gemini_conn.prompt(
                            PromptRequest(
                                sessionId=session.sessionId,
                                prompt=[text_block(DOCUMENTATION_PROMPT)],
                            )
                        )
                    )

                    # Wait for docs task and yield its events
                    while True:
                        if docs_task.done():
                            while not self._event_queue.empty():
                                update = await self._event_queue.get()
                                event_text = self._format_update(update)
                                if event_text:
                                    yield Event(
                                        author='coding',
                                        content=types.Content(parts=[types.Part(text=event_text)])
                                    )
                            break

                        try:
                            update = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                            event_text = self._format_update(update)
                            if event_text:
                                yield Event(
                                    author='coding',
                                    content=types.Content(parts=[types.Part(text=event_text)])
                                )
                        except asyncio.TimeoutError:
                            continue

                    # Build docker image - use repo/directory if configured, otherwise just directory
                    if self.docker_repo and directory:
                        image_tag = f"{self.docker_repo}/{directory}"
                    elif directory:
                        image_tag = directory
                    else:
                        image_tag = None

                    if image_tag:
                        yield Event(
                            author='coding',
                            content=types.Content(parts=[types.Part(text=f"[Docker] Building image: {image_tag}")])
                        )

                        try:
                            # Build docker image directly
                            process = await asyncio.create_subprocess_exec(
                                'docker', 'build', '-t', image_tag, '.',
                                cwd=dir_path,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.STDOUT
                            )

                            # Stream output line by line
                            while True:
                                line = await process.stdout.readline()
                                if not line:
                                    break
                                output = line.decode('utf-8').rstrip()
                                if output:
                                    yield Event(
                                        author='coding',
                                        content=types.Content(parts=[types.Part(text=f"[Docker] {output}")])
                                    )

                            await process.wait()

                            if process.returncode == 0:
                                yield Event(
                                    author='coding',
                                    content=types.Content(parts=[types.Part(text=f"[Docker] Successfully built image: {image_tag}")])
                                )
                            else:
                                yield Event(
                                    author='coding',
                                    content=types.Content(parts=[types.Part(text=f"[Docker] Build failed with exit code: {process.returncode}")])
                                )
                        except Exception as e:
                            yield Event(
                                author='coding',
                                content=types.Content(parts=[types.Part(text=f"[Docker] Error building image: {e}")])
                            )

                    break

                # Process event
                for task in done:
                    if task != prompt_task:
                        update = task.result()
                        event_text = self._format_update(update)
                        if event_text:
                            yield Event(
                                author='coding',
                                content=types.Content(parts=[types.Part(text=event_text)])
                            )

        except Exception as e:
            yield Event(
                author='coding',
                content=types.Content(parts=[types.Part(text=f"Error: {e}")])
            )
        finally:
            # Cancel prompt task if still running
            if not prompt_task.done():
                prompt_task.cancel()

    def _format_update(self, update) -> Optional[str]:
        """Format session update into text."""
        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                return update.content.text
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                return f"[Thought] {update.content.text}"
        elif isinstance(update, ToolCallStart):
            return f"[Tool] {update.title}"
        elif isinstance(update, ToolCallProgress):
            if update.status == "completed":
                return f"[Tool] Completed"
        return None


root_agent = CodingAgent(name='coder', root_dir=os.getcwd())

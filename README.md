# CodingAgent

An autonomous coding agent that leverages Gemini CLI to handle software development tasks with automatic Dockerfile creation, documentation updates, and Docker image building.

## Overview

`CodingAgent` is a Python-based agent built on top of Google's ADK (Agent Development Kit) that integrates with Gemini CLI via the Agent Communication Protocol (ACP). It provides an automated workflow for software development tasks including code generation, containerization, and documentation.

## Features

- **Automated Code Generation**: Processes user requests through Gemini CLI to generate code and scripts
- **Automatic Dockerfile Creation**: After processing user requests, automatically creates or updates appropriate Dockerfiles
- **Auto-Documentation**: Maintains a `GEMINI.md` file with session information and changes
- **Docker Image Building**: Automatically builds Docker images after creating Dockerfiles
- **File System Integration**: Supports reading and writing files through the ACP protocol
- **Auto-Approval**: Automatically approves permission requests for streamlined operation

## Architecture

### Core Components

1. **GeminiClient**: ACP client that handles communication with Gemini CLI
   - Auto-approves permissions
   - Handles file read/write operations
   - Queues session updates as events
   - Silently ignores ACP exceptions for robustness

2. **CodingAgent**: Main agent class that orchestrates the workflow
   - Manages Gemini CLI subprocess
   - Processes user requests
   - Executes automated follow-up tasks
   - Streams events to the caller

### Workflow

```
User Request
    “
Process with Gemini CLI
    “
Create/Update Dockerfile
    “
Update GEMINI.md Documentation
    “
Build Docker Image (if directory specified)
    “
Return Results
```

## Configuration

### Parameters

- **`name`** (str): Agent name identifier
- **`root_dir`** (str): Root directory for all agent operations
- **`docker_repo`** (Optional[str]): Docker repository prefix for image tags (default: `None`)

### Docker Image Tagging

The agent uses intelligent tagging based on configuration:

| Scenario | Image Tag |
|----------|-----------|
| `docker_repo` set + directory specified | `{docker_repo}/{directory}` |
| `docker_repo` is `None` + directory specified | `{directory}` |
| No directory | No Docker build |

## Usage

### Basic Setup

```python
from agent.agent import CodingAgent

# Create agent without Docker repository
agent = CodingAgent(
    name='coder',
    root_dir='/path/to/projects'
)

# Create agent with Docker repository
agent = CodingAgent(
    name='coder',
    root_dir='/path/to/projects',
    docker_repo='myrepo'
)
```

### Running the Agent

The agent processes requests through its async generator interface:

```python
from google.adk.agents.invocation_context import InvocationContext

async for event in agent._run_async_impl(context):
    # Handle events from the agent
    print(event.content.parts[0].text)
```

## File System Capabilities

The agent supports ACP file system operations:

- **Read Text Files**: Read file contents with absolute paths
- **Write Text Files**: Write content to files with automatic directory creation
- **Path Validation**: Ensures all paths are absolute for security

## Event Types

The agent yields various event types during execution:

- **Agent Messages**: Text responses from Gemini CLI
- **Agent Thoughts**: Internal reasoning and planning
- **Tool Calls**: Notifications when tools are being used
- **Docker Events**: Build progress and status updates (prefixed with `[Docker]`)

## Requirements

- Python 3.9+
- Google ADK (Agent Development Kit)
- Gemini CLI with ACP support (`gemini --experimental-acp`)
- Docker (for image building)
- ACP Protocol library

## Error Handling

The agent implements robust error handling:

- All ACP exceptions are caught and handled gracefully
- Failed file operations return empty responses instead of crashing
- Docker build errors are reported but don't halt execution
- Session updates continue even if individual operations fail

## Example Session

```python
# User request: "create script to generate 1000 random integers"

# Agent will:
# 1. Create generate_random_integers.py
# 2. Create/update Dockerfile
# 3. Update GEMINI.md with session summary
# 4. Build Docker image tagged as 'test' (if directory is 'test')
```

## Advanced Configuration

### Custom Follow-up Prompts

The agent uses configurable prompts for automated tasks:

```python
DOCKERFILE_PROMPT = "Create or update apropriate Dockerfile"
DOCUMENTATION_PROMPT = "Update GEMINI.md with informations in this session"
```

These can be modified at the module level to customize behavior.

### Session Management

Each agent invocation creates a new Gemini CLI session with:
- Working directory set to `{root_dir}/{directory}`
- Empty MCP servers configuration
- Automatic cleanup on completion

## Limitations

- Requires Gemini CLI to be installed and accessible in PATH
- Directory must be specified in session state for Docker builds
- File operations are limited to text files
- Docker build requires Dockerfile to exist in the working directory

## License

See project license file for details.

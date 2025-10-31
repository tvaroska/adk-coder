# Load env variables
from typing import List

from dotenv import load_dotenv
load_dotenv()

import time

import os
import uuid
import json
import argparse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions

from google.genai.types import Content, Part

from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import export
from opentelemetry.sdk.trace import TracerProvider

from agent.agent import root_agent

# Set tracing to Cloud Traces
# provider = TracerProvider()
# processor = export.BatchSpanProcessor(
#     CloudTraceSpanExporter(project_id=os.getenv("TRACES_PROJECT"))
# )
# provider.add_span_processor(processor)
# trace.set_tracer_provider(provider)

APP_NAME = "coding"
USER_ID = "testing"

session_service = InMemorySessionService()

# In memory now, replace with GcsArtifactService(bucket_name=os.gentenv("COSCIENTIST_BUCKET"))
artifact_service = InMemoryArtifactService()

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
    artifact_service=artifact_service
)

async def single_run(prompts: List[str]):
    session_id = str(uuid.uuid4())
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )

    if 'state' in prompts and len(prompts['state'])>0:
        actions_with_update = EventActions(state_delta=prompts['state'])

        system_event = Event(
            invocation_id="state_update",
            author="system",
            actions=actions_with_update,
            timestamp=time.time()
        )

        await session_service.append_event(session, system_event)

    for line in prompts['queries']:

        print("\n" + "="*60)
        print("USER INPUT:")
        print("="*60)
        print(line)

        user_content = Content(
            role="user", parts=[Part(text=line)]
        )

        final_response_content = "No response"
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=user_content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_response_content = event.content.parts[0].text

        print("\n" + "="*60)
        print("AGENT RESPONSE:")
        print("="*60)
        print(final_response_content)

async def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='tests.jsonl', help='Input file path')
    args = parser.parse_args()

    with open(args.input,'r') as f:
        test_case = json.load(f)

    # sequential to limit load on models
    await single_run(test_case)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
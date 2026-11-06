"""The loop accepts this small protocol, including future self-hosted models."""
from __future__ import annotations
import asyncio, json
from .types import ModelReply, ToolCall

class ScriptedModel:
    """A deterministic test/demo driver; it does not perform language inference."""
    def __init__(self, replies): self.replies=list(replies);self.requests=[]
    async def respond(self,messages,tools,*,timeout,max_output_tokens):
        self.requests.append({'messages':messages,'tools':tools})
        if not self.replies:raise RuntimeError('Scripted demonstration exhausted')
        return self.replies.pop(0)

class OpenAICompatibleModel:
    def __init__(self, *, base_url, api_key, model, reasoning_effort=None):
        from openai import AsyncOpenAI
        self.client=AsyncOpenAI(base_url=base_url,api_key=api_key,max_retries=0)
        self.model=model;self.reasoning_effort=reasoning_effort
    async def respond(self,messages,tools,*,timeout,max_output_tokens):
        args={'model':self.model,'messages':messages,'max_tokens':max_output_tokens,'timeout':timeout}
        if tools:args.update(tools=tools,tool_choice='auto')
        if self.reasoning_effort:args['reasoning_effort']=self.reasoning_effort
        response=await self.client.chat.completions.create(**args)
        message=response.choices[0].message;calls=[]
        for call in message.tool_calls or []:
            try:
                arguments=json.loads(call.function.arguments,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite argument')))
            except (ValueError,TypeError):arguments={'__invalid_json__':True}
            calls.append(ToolCall(call.id,call.function.name,arguments))
        usage={'requests':1,'input_tokens':response.usage.prompt_tokens,'output_tokens':response.usage.completion_tokens,'total_tokens':response.usage.total_tokens} if response.usage else {'requests':1,'unknown_usage_requests':1}
        # Only visible answer text and tool calls cross this boundary; no reasoning fields.
        return ModelReply(message.content or '',calls,usage)

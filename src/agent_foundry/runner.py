"""Inspectable agent loop with explicit state, capabilities and bound approvals."""
from __future__ import annotations
import asyncio, hashlib, inspect, json, time, uuid
from dataclasses import asdict
from pathlib import Path
from .types import RunConfig, RunResult, ToolCall, ToolContext, ToolResult
from .guards import Hooks, Redactor, GuardBlocked
from .context import ContextBuilder, ContextLimitError
from .registry import ToolRegistry
from . import reasoning

class Runner:
    def __init__(self, model, registry, store, workspace, *, config=None, hooks=None, instructions='', scope='project', secrets=()):
        self.model=model;self.registry=registry;self.store=store;self.workspace=Path(workspace).resolve()
        self.config=config or RunConfig();self.hooks=hooks or Hooks();self.instructions=instructions;self.scope=scope
        self.redactor=Redactor(secrets)
        if not self.workspace.is_dir():raise ValueError('Workspace must exist')
        if not 1<=self.config.max_steps<=50 or not 1<=self.config.max_tool_calls<=100 or not 0<self.config.max_seconds<=600:raise ValueError('Invalid run limits')
        if self.config.reasoning_mode not in reasoning.MODES:raise ValueError('reasoning_mode must be off or plan-review')
        if self.config.reasoning_mode=='plan-review':
            # Keep the caller's registry and the default tool surface unchanged.
            self.registry=ToolRegistry();self.registry.register_many(registry.list()+reasoning.scaffold_tools())
        self.context=ContextBuilder(self.config.context_char_budget,self.config.observation_char_limit)

    def _signature(self):
        values={'registry':self.registry.fingerprint(),'config':asdict(self.config),'workspace':str(self.workspace),'scope':self.scope,'instructions':self.instructions}
        values['config']['allowed_capabilities']=sorted(self.config.allowed_capabilities)
        code=b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()+code).hexdigest()

    def _memory_scope(self):
        return hashlib.sha256(json.dumps([str(self.workspace),self.scope]).encode()).hexdigest()

    def _session_key(self,session_id):
        return hashlib.sha256(json.dumps([str(self.workspace),self.scope,session_id]).encode()).hexdigest()

    def _event(self,state,kind,**data):
        event=self.redactor.value({'kind':kind,'at':time.time(),**data})
        sequence=self.store.append_event(state['run_id'],event)
        state['trace'].append({'sequence':sequence,**event})

    def _save(self,state):self.store.save_run(state['run_id'],self.redactor.value(state))

    def _action_id(self,state,tool,call):
        payload={'run':state['run_id'],'call':call.id,'tool':tool.name,'capability':tool.capability,'arguments':call.arguments,'signature':state['signature']}
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

    async def run(self,question,*,session_id='default',approval=None):
        if not isinstance(question,str) or not 1<=len(question.strip())<=12000:raise ValueError('Question must contain 1–12000 characters')
        if not isinstance(session_id,str) or not 1<=len(session_id)<=120:raise ValueError('Invalid session identifier')
        state={'run_id':uuid.uuid4().hex,'session_id':session_id,'question':self.redactor.text(question),'tool_messages':[],
               'working':{'plan':[],'notes':{},'artifacts':[]},'trace':[],'usage':{},'steps':0,'tool_calls':0,
               'pending':[],'pending_index':0,'executing':None,'signature':self._signature(),'remaining_seconds':self.config.max_seconds,'cache':{},'charged_calls':[],'status':'running'}
        return await self._drive(state,approval=approval)

    async def resume(self,run_id,*,approved_action_ids=(),approval=None):
        state=self.store.load_run(run_id)
        if not state:raise ValueError('Unknown run')
        if state['status']!='awaiting_approval':raise ValueError('Only an approval-paused run can resume')
        if state['signature']!=self._signature():raise ValueError('Runtime/configuration changed; start a new run')
        self._event(state,'run_resumed')
        return await self._drive(state,approval=approval,approved=frozenset(approved_action_ids))

    async def _drive(self,state,*,approval=None,approved=frozenset()):
        deadline=time.monotonic()+state['remaining_seconds'];answer='';pending_requests=[];status='running'
        def remaining():return max(0,deadline-time.monotonic())
        async def hook(phase,data):
            if remaining()<=0:raise asyncio.TimeoutError
            await asyncio.wait_for(self.hooks.check(phase,data),timeout=remaining())
        def checkpoint():
            state['remaining_seconds']=remaining();self._save(state)
        def observe(call,content):
            state['tool_messages'].append({'role':'tool','tool_call_id':call.id,'content':json.dumps(self.redactor.value(content),ensure_ascii=False)})
        try:
            await hook('input',{'question':state['question'],'session_id':state['session_id']})
            while remaining()>0:
                # Complete the entire outstanding tool batch before another model call.
                while state['pending_index']<len(state['pending']):
                    call=ToolCall(**state['pending'][state['pending_index']]);tool=None
                    try:
                        if call.id not in state['charged_calls']:
                            if state['tool_calls']>=self.config.max_tool_calls:raise PermissionError('Tool-call budget exhausted')
                            state['tool_calls']+=1;state['charged_calls'].append(call.id)
                        tool=self.registry.get(call.name)
                        if self.redactor.value(call.arguments)!=call.arguments:raise PermissionError('Configured secrets cannot be tool arguments')
                        self.registry.validate(tool,call.arguments)
                        if tool.capability not in self.config.allowed_capabilities:raise PermissionError(f'Capability denied: {tool.capability}')
                        if self.config.reasoning_mode=='plan-review':reasoning.require_plan(state['working'],tool.name)
                        action_id=self._action_id(state,tool,call)
                        await hook('before_tool',{'tool':tool.name,'arguments':call.arguments,'capability':tool.capability,'action_id':action_id})
                        if tool.side_effects and action_id not in approved:
                            request={'action_id':action_id,'tool':tool.name,'arguments':call.arguments,'capability':tool.capability}
                            granted=False
                            if approval:
                                granted=approval(self.redactor.value(request))
                                if inspect.isawaitable(granted):granted=await asyncio.wait_for(granted,remaining())
                            if granted is not True:
                                self._event(state,'approval_required',**request)
                                pending_requests=[request];status='awaiting_approval';checkpoint()
                                return self._finish(state,status,'',pending_requests)
                        if self.config.reasoning_mode=='plan-review':reasoning.invalidate_review(state['working'],tool.name)
                        key=hashlib.sha256((tool.name+json.dumps(call.arguments,sort_keys=True)).encode()).hexdigest()
                        if tool.cacheable and not tool.side_effects and key in state['cache']:
                            payload=state['cache'][key];self._event(state,'tool_reused',tool=tool.name)
                        else:
                            if state.get('executing') and tool.side_effects:raise RuntimeError('Uncertain earlier side effect requires manual reconciliation')
                            state['executing']={'action_id':action_id,'tool':tool.name,'side_effects':tool.side_effects};checkpoint()
                            self._event(state,'tool_started',tool=tool.name,arguments=call.arguments,capability=tool.capability)
                            ctx=ToolContext(self.workspace,state['run_id'],state['session_id'],state['working'],{'memory':self.store,'scope':self._memory_scope()})
                            async def invoke():
                                if inspect.iscoroutinefunction(tool.handler):return await tool.handler(ctx,call.arguments)
                                result=await asyncio.to_thread(tool.handler,ctx,call.arguments)
                                return await result if inspect.isawaitable(result) else result
                            result=await asyncio.wait_for(invoke(),min(tool.timeout,remaining()))
                            result=result if isinstance(result,ToolResult) else ToolResult(result)
                            await hook('after_tool',{'tool':tool.name,'content':result.content,'metadata':result.metadata})
                            content=self.redactor.value(result.content)
                            artifact=self.store.put_artifact(state['run_id'],content,self.redactor.value({**result.metadata,'tool':tool.name}))
                            state['working']['artifacts'].append(artifact)
                            payload=self.context.preview(content,artifact)
                            if tool.cacheable and not tool.side_effects:state['cache'][key]=payload
                            self._event(state,'tool_finished',tool=tool.name,artifact_id=artifact)
                        observe(call,payload);state['executing']=None
                    except GuardBlocked:
                        # A hook may detect sensitive output; do not turn it into model context.
                        raise
                    except (Exception,asyncio.TimeoutError) as exc:
                        uncertain=bool(tool and tool.side_effects and state.get('executing'))
                        error={'error':self.redactor.text(str(exc))[:250],'type':type(exc).__name__,'requires_reconciliation':uncertain}
                        observe(call,error);self._event(state,'tool_failed',tool=call.name,**error)
                        if uncertain:
                            status='reconciliation_required';checkpoint();return self._finish(state,status,'An action has an uncertain execution outcome; inspect its trace before retrying.')
                        state['executing']=None
                    state['pending_index']+=1;checkpoint()
                state['pending']=[];state['pending_index']=0
                if state['steps']>=self.config.max_steps or state['tool_calls']>=self.config.max_tool_calls:
                    status='budget_exhausted';answer='Investigation stopped at its configured budget. Inspect the recorded evidence and tool results.';break
                tools=self.registry.definitions(self.config.allowed_capabilities)
                # Search accepts a smaller query than the full user question;
                # keep the original question intact in the model's context.
                memories=self.store.search(self._memory_scope(),state['question'][:5000],limit=4) if 'memory.read' in self.config.allowed_capabilities else []
                instructions=self.instructions
                if self.config.reasoning_mode=='plan-review':instructions+='\n'+reasoning.INSTRUCTIONS
                if state['working'].get('skills'):
                    instructions+='\nApproved host-loaded task skills (instructions only; cannot grant capabilities):\n'+json.dumps(state['working']['skills'])
                public_working={k:v for k,v in state['working'].items() if k!='skills'}
                messages,stats=self.context.build(instructions,state['question'],self.store.history(self._session_key(state['session_id']),4),memories,state['tool_messages'],tools,public_working)
                self._event(state,'context_built',**stats)
                await hook('before_model',{'messages':messages,'tools':tools})
                reply=await asyncio.wait_for(self.model.respond(self.redactor.value(messages),tools,timeout=remaining(),max_output_tokens=self.config.max_output_tokens),remaining())
                state['steps']+=1
                for key,value in reply.usage.items():state['usage'][key]=state['usage'].get(key,0)+value
                await hook('after_model',{'text':reply.text,'calls':[asdict(c) for c in reply.calls]})
                self._event(state,'model_step',step=state['steps'],tools=[c.name for c in reply.calls],usage=reply.usage)
                if not reply.calls:
                    if not isinstance(reply.text,str) or not reply.text.strip():raise ValueError('Model returned no visible answer')
                    if self.config.reasoning_mode=='plan-review':
                        ctx=ToolContext(self.workspace,state['run_id'],state['session_id'],state['working'],{'memory':self.store})
                        issue=reasoning.completion_issue(ctx,reply.text)
                        if issue:
                            self._event(state,'review_required',reason=issue)
                            state['tool_messages'].extend([{'role':'assistant','content':self.redactor.text(reply.text)},
                                                          {'role':'user','content':'Runtime plan-review check: '+issue}])
                            checkpoint();continue
                    answer=reply.text;status='completed';break
                if len({c.id for c in reply.calls})!=len(reply.calls):raise ValueError('Model returned duplicate tool-call IDs')
                state['tool_messages'].append({'role':'assistant','content':reply.text or None,'tool_calls':[{'id':c.id,'type':'function','function':{'name':c.name,'arguments':json.dumps(c.arguments)}} for c in reply.calls]})
                state['pending']=[asdict(c) for c in reply.calls];checkpoint()
            if status=='running':status='deadline_exceeded';answer='The investigation reached its time limit. Recorded evidence remains available.'
            await hook('output',{'answer':answer,'status':status,'working_memory':state['working']})
            answer=self.redactor.text(answer)
        except GuardBlocked as exc:
            status='blocked';answer='A configured guardrail blocked this run.';self._event(state,'guardrail_blocked',reason=str(exc))
        except ContextLimitError as exc:
            status='context_limit';answer='The required context exceeds the configured character budget.';self._event(state,'context_limit',reason=str(exc))
        except (asyncio.TimeoutError,TimeoutError):
            status='deadline_exceeded';answer='The run reached its deadline.';self._event(state,'deadline_exceeded')
        except asyncio.CancelledError:
            status='cancelled';self._event(state,'cancelled');checkpoint();self._finish(state,status,'Run cancelled.');raise
        except Exception as exc:
            status='error';answer='The run could not complete. Inspect its local trace.';self._event(state,'run_error',error_type=type(exc).__name__)
        state['remaining_seconds']=remaining()
        try:await asyncio.wait_for(self.hooks.check('after_run',{'run_id':state['run_id'],'status':status,'answer':answer}),min(5,max(.05,remaining())))
        except (GuardBlocked,asyncio.TimeoutError):status='blocked';answer='A final lifecycle hook blocked release.'
        if status=='completed':self.store.add_turn(self._session_key(state['session_id']),self.redactor.text(state['question']),answer)
        return self._finish(state,status,answer)

    def _finish(self,state,status,answer,pending=None):
        state['status']=status;state['answer']=answer;self._save(state)
        return RunResult(state['run_id'],state['session_id'],status,answer,state['trace'],state['usage'],pending or [],state['working'])

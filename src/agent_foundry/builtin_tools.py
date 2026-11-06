"""Trusted native capabilities; arbitrary code belongs in an isolated executor."""
from pathlib import Path
import json
from .types import Tool, ToolResult

DENIED={'.env','.git','.foundry','.venv','.bootstrap','__pycache__'}

def scoped_path(workspace,relative):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts or any(x in DENIED or x.startswith('.env.') for x in path.parts):raise ValueError('Path is outside the readable workspace policy')
    root=Path(workspace).resolve();resolved=(root/path).resolve()
    if not resolved.is_relative_to(root):raise ValueError('Symlink escapes the workspace')
    if resolved.suffix.lower() in {'.pem','.key','.p12'}:raise ValueError('Private key files are not readable tools')
    return resolved


def native_tools():
    def listing(ctx,args):
        folder=scoped_path(ctx.workspace,args.get('path','.'))
        return {'entries':[{'path':p.relative_to(ctx.workspace).as_posix(),'directory':p.is_dir()} for p in sorted(folder.iterdir()) if p.name not in DENIED and not p.name.startswith('.env')][:100]}
    def read(ctx,args):
        path=scoped_path(ctx.workspace,args['path'])
        if not path.is_file() or path.stat().st_size>250000:raise ValueError('Use a readable text file below 250 KB')
        return {'path':args['path'],'text':path.read_text(encoding='utf-8'),'provenance':'workspace file'}
    def artifact(ctx,args):
        record=ctx.services['memory'].get_artifact(args['artifact_id'])
        if record['run_id']!=ctx.run_id:raise ValueError('Artifact does not belong to this run')
        text=json.dumps(record['content'],ensure_ascii=False)
        start=args.get('offset',0);limit=args.get('limit',3000)
        return {'artifact_id':args['artifact_id'],'offset':start,'text':text[start:start+limit],'has_more':start+limit<len(text)}
    def working(ctx,args):
        ctx.state.setdefault('notes',{})[args['key']]=args['text']
        return {'saved':True,'note':args['key'],'scope':'current run only; public task notes'}
    def search_memory(ctx,args):return ctx.services['memory'].search(ctx.services['scope'],args['query'],limit=5)
    def remember(ctx,args):
        return ctx.services['memory'].remember(ctx.services['scope'],args['key'],args['text'],f'approved_run:{ctx.run_id}',args.get('ttl_seconds'))
    def forget(ctx,args):return {'deleted':ctx.services['memory'].forget(ctx.services['scope'],args['key'])}
    obj=lambda props,required: {'type':'object','properties':props,'required':required,'additionalProperties':False}
    string=lambda maximum: {'type':'string','minLength':1,'maxLength':maximum}
    return [
        Tool('list_files','List available files in an approved workspace directory.',obj({'path':string(300)},[]),listing,'files.read'),
        Tool('read_file','Read a text file from the approved workspace; secrets and escaping symlinks are denied.',obj({'path':string(300)},['path']),read,'files.read'),
        Tool('read_artifact','Read another slice of a complete tool result stored during this run.',obj({'artifact_id':string(100),'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':100,'maximum':6000}},['artifact_id']),artifact),
        Tool('working_note','Save a concise public task note or checklist, never private chain-of-thought.',obj({'key':string(100),'text':string(2000)},['key','text']),working,'working.write',cacheable=False),
        Tool('search_memory','Search explicitly saved long-term records within this project scope.',obj({'query':string(1000)},['query']),search_memory,'memory.read'),
        Tool('remember','Save a reviewed fact or preference. Requires explicit capability and approval.',obj({'key':string(100),'text':string(4000),'ttl_seconds':{'type':'integer','minimum':1,'maximum':31536000}},['key','text']),remember,'memory.write',True,cacheable=False),
        Tool('forget_memory','Delete a saved long-term record in this scope. Requires explicit approval.',obj({'key':string(100)},['key']),forget,'memory.write',True,cacheable=False),
    ]

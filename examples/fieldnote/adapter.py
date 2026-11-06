"""Domain factories: the general runtime has no sensor or station assumptions."""
import json, re, sqlite3, time
from pathlib import Path
from decimal import Decimal
from agent_foundry.types import Tool, ToolResult

ROOT=Path(__file__).resolve().parent
SCHEMA={'type':'object','properties':{'sql':{'type':'string','minLength':1,'maxLength':8000}},'required':['sql'],'additionalProperties':False}

def sql_factory(options):
    def query(ctx,args):
        db=ROOT/'data/operations.db'
        con=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
        try:
            con.execute('PRAGMA query_only=ON');con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,100000)
            tables={'sites','sensors','readings','maintenance'}
            functions={'avg','sum','count','min','max','round','coalesce','nullif','abs','strftime','date','group_concat','like'}
            touched=set()
            def allow(action,a,b,d,t):
                if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
                if action==sqlite3.SQLITE_READ and a in tables and d=='main':
                    touched.add(a);return sqlite3.SQLITE_OK
                if action==sqlite3.SQLITE_FUNCTION and str(b).lower() in functions:return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            con.set_authorizer(allow);started=time.monotonic()
            con.set_progress_handler(lambda:int(time.monotonic()-started>1.5),1000)
            cursor=con.execute(args['sql']);rows=[dict(r) for r in cursor.fetchmany(201)]
            if not cursor.description or not touched:raise ValueError('Read an approved observations table')
            if len({x[0] for x in cursor.description})!=len(cursor.description):raise ValueError('Use distinct column aliases')
            return ToolResult({'columns':[x[0] for x in cursor.description],'rows':rows[:200],'truncated':len(rows)>200,'query':args['sql']},{'source':'fieldnote:operations.db','read_only':True})
        finally:con.close()
    return Tool('query_observations','Read fictional sensor observations. Default valid readings, preserve units and UTC dates. Tables: sites,sensors,readings,maintenance.',SCHEMA,query)

def search_factory(options):
    def search(ctx,args):
        terms=set(re.findall(r'[a-z0-9]+',args['query'].lower()))
        docs=[]
        for path in (ROOT/'data/documents').glob('*.md'):
            text=path.read_text();score=sum(text.lower().count(t) for t in terms if len(t)>2)
            if score:docs.append({'document_id':path.stem,'text':text,'score':score})
        docs.sort(key=lambda x:x['score'],reverse=True)
        return ToolResult({'documents':docs[:4]},{'source':'fieldnote:field_reports','ranking':'lexical demonstration'})
    return Tool('search_reports','Find original field reports and protocols for the fictional research stations.',{'type':'object','properties':{'query':{'type':'string','minLength':1,'maxLength':1000}},'required':['query'],'additionalProperties':False},search)

def calculator_factory(options):
    def calculate(ctx,args):
        # References identify actual SQL result fields, not free model-supplied numbers.
        values=[]
        for ref in args['operands']:
            a=ctx.services['memory'].get_artifact(ref['artifact_id'])
            if a['run_id']!=ctx.run_id:raise ValueError('Operand artifact belongs to another run')
            value=a['content']['rows'][ref.get('row',0)][ref['field']]
            if value is None or isinstance(value,bool):raise ValueError('Missing/boolean operand')
            n=Decimal(str(value))
            if not n.is_finite():raise ValueError('Non-finite operand')
            values.append(n)
        if args['operation']=='sum':result=sum(values)
        elif args['operation']=='mean':result=sum(values)/len(values)
        elif len(values)!=2:raise ValueError('This operation requires two operands')
        elif args['operation']=='difference':result=values[0]-values[1]
        elif values[1]==0:raise ValueError('Zero denominator')
        else:result=values[0]/values[1]
        return ToolResult({'operation':args['operation'],'value':str(result),'source_operands':args['operands']},{'source':'fieldnote:checked_calculation'})
    operand={'type':'object','properties':{'artifact_id':{'type':'string'},'row':{'type':'integer','minimum':0},'field':{'type':'string'}},'required':['artifact_id','field'],'additionalProperties':False}
    return Tool('calculate_observations','Calculate from numeric cells in evidence artifacts returned in this run.',{'type':'object','properties':{'operation':{'enum':['sum','mean','difference','ratio'],'type':'string'},'operands':{'type':'array','items':operand,'minItems':1,'maxItems':20}},'required':['operation','operands'],'additionalProperties':False},calculate)

FACTORIES={'fieldnote.sql':sql_factory,'fieldnote.search':search_factory,'fieldnote.calculate':calculator_factory}

def instructions():
    con=sqlite3.connect(ROOT/'data/operations.db')
    try:schema=[r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='table'")]
    finally:con.close()
    return 'Research the fictional station dataset. Cite artifact IDs and document IDs; do not invent support. '+(ROOT/'data/semantics.md').read_text()+'\nSchema:\n'+'\n'.join(schema)

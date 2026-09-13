"""Dependency-free MCP stdio transport for the versioned service operations.

Implements initialize, ping, tools and a capability resource. Does not advertise
MCP Tasks, HTTP sessions, prompts or features this implementation does not have.
"""
import json
import sys
from .. import __version__
from .contracts import OPERATIONS, envelope, Fault, strict_object, output_schema

PROTOCOLS=('2025-11-25','2025-06-18','2025-03-26','2024-11-05')
OUTPUT={'type':'object','required':['schema_version','request_id','status','data','limitations','next_actions'],
        'properties':{'schema_version':{'type':'string'},'request_id':{'type':'string'},'status':{'type':'string','enum':['ok','error']},
                      'data':{'type':'object'},'limitations':{'type':'array'},'next_actions':{'type':'array'},'error':{'type':'object'}}}


def handle(service,message,readonly=False):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0':
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid JSON-RPC request'}}
    if 'id' not in message:return None
    identifier=message['id'];method=message.get('method');params=message.get('params',{})
    def response(result):return {'jsonrpc':'2.0','id':identifier,'result':result}
    def error(code,text):return {'jsonrpc':'2.0','id':identifier,'error':{'code':code,'message':text}}
    if not isinstance(params,dict):return error(-32602,'Params must be an object')
    if method=='initialize':
        requested=params.get('protocolVersion')
        return response({'protocolVersion':requested if requested in PROTOCOLS else PROTOCOLS[0],
                         'capabilities':{'tools':{'listChanged':False},'resources':{'subscribe':False,'listChanged':False}},
                         'serverInfo':{'name':'worldfuzz','version':__version__},
                         'instructions':'Use the bare tool name from tools/list, e.g. get_result_report. If the host has ServerName and ToolName fields, select the configured server in ServerName and put only get_result_report in ToolName; never server/get_result_report. Adapter operations such as solve_navigation are goal.operation parameters for compile_goal or run_goal, not tools. Moving AI case_indices are zero-based: movingai:8 selects index 8; inspect find_resources. export_result only prepares a plan, with no run or artifact; use execute_plan or run_goal for execution and confirm export from completed execution with existing artifacts. Use describe_operation for a tool schema and obey concrete error next_actions. Campaign requests require a campaign, not standalone runs. Do not substitute unknown conversion, replay or forbidden held-out use. Nanoseconds are decimal strings. Source reward is not a verified physical outcome.'})
    if method=='ping':return response({})
    if method=='tools/list':
        return response({'tools':[{'name':name,'description':s['description'],'inputSchema':s['inputSchema'],'outputSchema':output_schema(name),
                                   'annotations':{'readOnlyHint':s['effect']=='read','destructiveHint':False,'openWorldHint':False}}
                                  for name,s in OPERATIONS.items() if not readonly or s['effect']=='read']})
    if method=='tools/call':
        name=params.get('name')
        if name not in OPERATIONS:
            prefixed=isinstance(name,str) and ('/' in name or name.startswith('mcp__'))
            value=envelope(error=Fault('UNSUPPORTED_OPERATION',
                'Use a bare tool name from tools/list; select the MCP server separately. Do not repeat the server prefix.' if prefixed else
                'Not a service/MCP tool. Adapter operations go in goal.operation of compile_goal.',
                next_action='revise_request' if prefixed else 'compile_goal'))
            return response({'content':[{'type':'text','text':json.dumps(value)}],'structuredContent':value,'isError':True})
        result=(envelope(error=Fault('ACCESS_DENIED','Read-only server')) if readonly and OPERATIONS[name]['effect']!='read'
                else service.call(name,params.get('arguments',{})))
        return response({'content':[{'type':'text','text':json.dumps(result,allow_nan=False)}],
                         'structuredContent':result,'isError':result['status']=='error'})
    if method=='resources/list':return response({'resources':[{'uri':'worldfuzz://capabilities','name':'Supported capabilities','mimeType':'application/json'}]})
    if method=='resources/read' and params.get('uri')=='worldfuzz://capabilities':
        result=service.call('get_capabilities')
        return response({'contents':[{'uri':'worldfuzz://capabilities','mimeType':'application/json','text':json.dumps(result)}]})
    return error(-32601,'Method not supported')


def serve(service,readonly=False,source=None,target=None):
    source=source or sys.stdin;target=target or sys.stdout
    initialized=False;ready=False
    while True:
        line=source.readline(2*1024*1024+1)
        if not line:break
        if len(line)>2*1024*1024:raise ValueError('MCP message exceeds 2 MiB')
        try:
            message=json.loads(line,object_pairs_hook=strict_object)
            method=message.get('method') if isinstance(message,dict) else None
            if method=='notifications/initialized' and initialized:ready=True
            if isinstance(message,dict) and 'id' in message and not ready and method not in ('initialize','ping'):
                result={'jsonrpc':'2.0','id':message['id'],'error':{'code':-32000,'message':'Initialize the MCP session first'}}
            else:
                result=handle(service,message,readonly)
                if method=='initialize' and result and 'result' in result:initialized=True
        except (ValueError,TypeError):result={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}}
        if result is not None:
            target.write(json.dumps(result,allow_nan=False)+'\n');target.flush()

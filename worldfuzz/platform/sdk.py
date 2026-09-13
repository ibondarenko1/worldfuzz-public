"""Small HTTP SDK using the same operation catalogue as CLI and MCP."""
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from .contracts import OPERATIONS, Fault, output_schema, validate


class Client:
    def __init__(self,base_url,token,timeout=15):
        self.base_url=base_url.rstrip('/');self.token=token;self.timeout=timeout

    def call(self,operation,arguments=None):
        spec=OPERATIONS[operation];args=dict(arguments or {})
        validate(args,spec['inputSchema'])
        route=spec['route']
        for key in list(args):
            if '{'+key+'}' in route:route=route.replace('{'+key+'}',quote(str(args.pop(key)),safe=''))
        body=json.dumps(args,allow_nan=False).encode() if spec['method']=='POST' else None
        request=Request(self.base_url+route,data=body,method=spec['method'],
                        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=self.timeout) as response:result=json.load(response)
        except HTTPError as exc:result=json.load(exc)
        try:validate(result,output_schema(operation),'$')
        except Fault as exc:raise Fault('RESPONSE_INVALID','Server response failed SDK validation: '+str(exc),exc.path) from exc
        return result

    def run_goal(self, goal, idempotency_key, *, resource_id=None, wait_seconds=20,
                 include_artifacts=False):
        """Execute the common typed workflow; no client-side planning or ID transfer."""
        arguments={'goal':goal,'idempotency_key':idempotency_key,
                   'wait_seconds':wait_seconds,'include_artifacts':include_artifacts}
        if resource_id is not None:arguments['resource_id']=resource_id
        previous=self.timeout
        try:
            self.timeout=max(previous,wait_seconds+15)
            return self.call('run_goal',arguments)
        finally:
            self.timeout=previous

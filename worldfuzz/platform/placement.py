"""Configured local service or remote common-protocol client; no parallel logic."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from .contracts import Fault, obj, validate


def connect(path):
    path=Path(path)
    if path.stat().st_size>65536:raise Fault('SCHEMA_INVALID','Placement config exceeds 64 KiB')
    from .contracts import strict_object
    config=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=strict_object)
    text={'type':'string','minLength':1,'maxLength':1024}
    validate(config,obj({'version':{'const':'1.0.0'},'kind':{'enum':['local','remote_http']},
        'endpoint':text,'token_env':text,'root':text,'store':text,'authority':text},['version','kind']))
    if config['kind']=='remote_http':
        if set(config)!={'version','kind','endpoint','token_env'}:raise Fault('SCHEMA_INVALID','Remote config requires only endpoint and token_env')
        url=urlsplit(config['endpoint'])
        if (url.username or url.password or url.query or url.fragment or url.path not in ('','/') or
            not url.hostname or (url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1')))):
            raise Fault('ACCESS_DENIED','Remote bearer requires HTTPS or loopback HTTP')
        token=os.environ.get(config['token_env'])
        if not token:raise Fault('ACCESS_DENIED','Placement token environment variable is absent')
        from .sdk import Client
        return Client(config['endpoint'],token)
    if 'endpoint' in config:raise Fault('SCHEMA_INVALID','Local config cannot contain an endpoint')
    if 'authority' in config:
        if set(config)!={'version','kind','authority','token_env'}:raise Fault('SCHEMA_INVALID','Local organization config requires authority and token_env')
        from .identity import IdentityStore
        return IdentityStore(config['authority']).session(os.environ.get(config['token_env'],''))
    if set(config)!={'version','kind','root','store'}:raise Fault('SCHEMA_INVALID','Local config requires root and store')
    from .service import Service
    return Service(config['root'],config['store'])

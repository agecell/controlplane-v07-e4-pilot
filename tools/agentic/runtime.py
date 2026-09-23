#!/usr/bin/env python3
"""Shared Control Plane v0.7 helpers. No background service; no secrets in error messages."""
from __future__ import annotations
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
IDENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
ROLES = ('BUILD','FIX','REVIEW','QA','PREP','INTEGRATE')
# Traceability records name the forge that produced the merge request, so a record
# stays readable after a team changes provider. Mirrors forge.PROVIDERS; kept literal
# so record validation does not depend on the adapter module being importable.
FORGE_PROVIDERS = ('gitlab','github')
class AEError(RuntimeError): pass

def need(ok, message):
    if not ok: raise AEError(message)

def text(v): return isinstance(v,str) and bool(v.strip()) and '__CONFIGURE__' not in v

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()

def fresh(value, seconds=900):
    try:
        age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(value.replace('Z','+00:00'))).total_seconds()
        need(0 <= age <= seconds, 'Attestation expired or is in the future; recheck actual state.')
    except (ValueError,TypeError,AttributeError): raise AEError('Timezone-aware check time required.')

def full(value):
    need(isinstance(value,str) and SHA.fullmatch(value), 'Full lowercase commit SHA required.')
    return value

def ident(value):
    need(isinstance(value,str) and IDENT.fullmatch(value), 'Invalid work/developer/invocation identifier.')
    return value

def run(argv, cwd=None, allowed=(0,), stdin=None):
    try:
        p=subprocess.run(argv,cwd=cwd,input=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         encoding='utf-8',errors='replace',timeout=90)
    except subprocess.TimeoutExpired as e: raise AEError('Command timed out; inspect actual state before retrying.') from e
    need(p.returncode in allowed, f'{Path(argv[0]).name} command failed (exit {p.returncode}); inspect privately.')
    return p.returncode,p.stdout.rstrip('\n')

def git(repo,*args,allowed=(0,),stdin=None): return run(['git','-C',str(repo),*args],allowed=allowed,stdin=stdin)

def root(repo): return Path(git(repo,'rev-parse','--show-toplevel')[1]).resolve()

def common(repo):
    p=Path(git(repo,'rev-parse','--git-common-dir')[1]);return (p if p.is_absolute() else Path(repo)/p).resolve()

def read_json(p):
    v=json.loads(Path(p).read_text(encoding='utf-8-sig'));need(isinstance(v,dict),'JSON object required.');return v

def atomic_json(p,data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex)
    try:
        with tmp.open('x',encoding='utf-8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally: tmp.unlink(missing_ok=True)

def ensure_no_links(p, stop):
    p=Path(p).absolute();stop=Path(stop).absolute()
    need(p.is_relative_to(stop), 'Path outside the approved coordinator.')
    while p != stop:
        try: is_reparse=bool(getattr(p.lstat(),'st_file_attributes',0)&0x400)
        except FileNotFoundError: is_reparse=False
        need(not p.is_symlink() and not is_reparse,'Symlink/junction/reparse paths are not supported.')
        p=p.parent

def config(repo):
    repo=root(repo);cfg=read_json(repo/'.agentic/project.json')
    need(cfg.get('schema_version')==5 and cfg.get('kit_version')=='0.7','Use a reviewed v0.7 schema-5 configuration; no implicit migration from v0.6.')
    try:
        import contract as _contract
    except ModuleNotFoundError:  # pragma: no cover
        from . import contract as _contract
    _contract.validate_assurance_policy(cfg.get('assurance_policy'))
    return cfg

def forge_ready(cfg):
    """Validate the forge block. Required for remote effects, optional for local work.

    v0.5 demanded gitlab_host and gitlab_repo before any operation, including pool
    init, which only creates local worktrees. A team evaluating Control Plane before
    choosing a forge had to either commit prematurely or put placeholder values into an
    approved configuration. The forge is now checked where it is actually used.
    """
    f=cfg.get('forge')
    need(isinstance(f,dict),'Configure the forge block: provider, host, repo.')
    need(set(f)=={'provider','host','repo'},'forge must declare exactly provider, host and repo.')
    need(f.get('provider') in ('gitlab','github'),'forge.provider must be gitlab or github.')
    for k in ('host','repo'):
        need(text(f.get(k)),f'Configure forge.{k}.')
    need(re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?',f['host']),'Invalid forge host.')
    need(not any(x in f['repo'] for x in ('://','..','?','#')) and '/' in f['repo'],'Use owner/name (GitHub) or group/project (GitLab) for forge.repo.')
    return f

def config_ready(cfg,remote=False):
    need(cfg.get('schema_version')==5 and cfg.get('kit_version')=='0.7','Use reviewed Control Plane v0.7 project schema 5.')
    need(cfg.get('configuration_approved') is True,'Project configuration needs maintainer setup.')
    try:
        import contract as _contract
    except ModuleNotFoundError:  # pragma: no cover
        from . import contract as _contract
    _contract.validate_assurance_policy(cfg.get('assurance_policy'), require_approved=True)
    for k in ('project_name','remote','target_branch','team_coordination_ref'):
        need(text(cfg.get(k)),f'Configure {k}.')
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',cfg['remote']),'Invalid remote name.')
    need(cfg.get('merge_mode') in ('AGENT_MERGE','DEVELOPER_REVIEW'),'Select a supported merge mode.')
    # Schema 5 splits required_checks into what every Story must satisfy and what is
    # derived from a Story's own evidence. Validate the shape here so a half-migrated
    # configuration fails at setup rather than at someone's first merge.
    _contract.validate_merge_policy_checks(cfg.get('merge_policy'))
    ref=cfg.get('backlog_authority_ref')
    need(ref is None or text(ref),'backlog_authority_ref must be null or a Git reference.')
    need(type(cfg.get('ci_required')) is bool, 'ci_required must be explicitly true or false.')
    need(type(cfg.get('max_local_builders')) is int and 1 <= cfg['max_local_builders'] <= 2, 'Local builder limit must be one or two.')
    if remote:
        forge_ready(cfg)
        need(cfg.get('remote_actions_ready') is True and text(cfg.get('remote_actions_review_ref')),'Remote effects have not been approved.')

def require_coordinator(repo,developer=None):
    repo=root(repo)
    state=common(repo)/'agentic/pool.json'
    if state.exists(): need(Path(read_json(state)['coordinator']).resolve()==repo,'Use the coordinator checkout, not a lane checkout.')
    branch=git(repo,'branch','--show-current')[1]
    prefix=f'ae/records/{ident(developer)}/' if developer else 'ae/records/'
    need(branch.startswith(prefix),'Coordinator must use its owner records branch.')
    return repo

@contextlib.contextmanager
def local_lock(repo):
    p=common(repo)/'agentic/runtime.lock';p.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError: raise AEError('Runtime lock exists. Investigate an active/interrupted action; never steal locks automatically.')
    with os.fdopen(fd,'w') as f: f.write(json.dumps({'pid':os.getpid(),'created_at':now()}))
    try: yield
    finally: p.unlink(missing_ok=True)

def worktrees(repo):
    rows=[];row={}
    for field in git(repo,'worktree','list','--porcelain','-z')[1].split('\0'):
        if not field:
            if row: rows.append(row);row={}
        else:
            key,_,val=field.partition(' ');row[key]=val
    if row:rows.append(row)
    return rows

def commit(repo,sha):
    full(sha);need(git(repo,'rev-parse','--verify',sha+'^{commit}')[1]==sha,'Commit not available or mismatched.');return sha

def local_ref(repo,ref):
    rc,_=git(repo,'show-ref','--verify','--quiet',ref,allowed=(0,1))
    if rc:return None
    need(git(repo,'symbolic-ref','-q',ref,allowed=(0,1))[0]==1,'Symbolic refs are not eligible.')
    return full(git(repo,'show-ref','--verify','--hash',ref)[1])

def remote_head(repo,remote,branch):
    rc,out=git(repo,'ls-remote','--exit-code','--heads',remote,'refs/heads/'+branch,allowed=(0,2))
    if rc==2:return None
    rows=[s.split('\t') for s in out.splitlines() if s]
    need(len(rows)==1 and rows[0][1]=='refs/heads/'+branch,'Ambiguous remote reference.')
    return full(rows[0][0])

def parse_remote(value):
    if '://' in value:
        u=urlsplit(value);need(u.scheme in ('ssh','https') and not u.password and not (u.scheme=='https' and u.username),'Use approved SSH/HTTPS without embedded credentials.')
        host=u.netloc.rsplit('@',1)[-1].lower();path=u.path.lstrip('/')
    else:
        m=re.fullmatch(r'(?:[\w.-]+@)?([\w.-]+):(.+)',value);need(m is not None,'Unsupported remote URL.')
        host=m[1].lower();path=m[2]
    return host,path.removesuffix('.git').rstrip('/')

def verify_remote(repo,cfg):
    a=git(repo,'remote','get-url','--all',cfg['remote'])[1].splitlines()
    b=git(repo,'remote','get-url','--push','--all',cfg['remote'])[1].splitlines()
    f=forge_ready(cfg)
    need(len(a)==len(b)==1 and parse_remote(a[0])==parse_remote(b[0])==(f['host'].lower(),f['repo']),'Fetch and push must identify the same configured forge repository.')

def forge_adapter(cfg):
    """Return the adapter for the configured provider.

    v0.5 called `glab` from this module directly. Provider-specific REST now lives in
    forge.py; everything here works with the normalized shapes it returns.
    """
    try:
        import forge as _forge
    except ModuleNotFoundError:  # pragma: no cover
        from . import forge as _forge
    import sys as _sys
    try:
        return _forge.adapter(_sys.modules[__name__], cfg)
    except _forge.ForgeError as e:
        raise AEError(str(e)) from e

def forge_call(cfg, fn, *args, **kwargs):
    """Run one adapter operation, translating its errors into AEError."""
    try:
        import forge as _forge
    except ModuleNotFoundError:  # pragma: no cover
        from . import forge as _forge
    try:
        return fn(*args, **kwargs)
    except _forge.ForgeError as e:
        raise AEError(str(e)) from e

def fetch_target(repo,cfg):
    ref=f'refs/remotes/{cfg["remote"]}/{cfg["target_branch"]}'
    git(repo,'fetch','--no-tags',cfg['remote'],f'refs/heads/{cfg["target_branch"]}:{ref}')
    return full(git(repo,'rev-parse',ref)[1])

def project_pull(repo,cfg,task):
    """Verify remote and pull request identity, returning (project, normalized pull).

    Same checks v0.5 performed against GitLab fields, now expressed against the
    normalized shape so both providers are held to the identical standard.
    """
    verify_remote(repo,cfg)
    a=forge_adapter(cfg)
    pull=forge_call(cfg,a.pull_request,repo,cfg,task)
    project=pull['_project']
    need(pull['same_project'],'Cross-project/fork or wrong pull request is unsupported.')
    need(pull['source_branch']==task['branch'] and pull['target_branch']==cfg['target_branch'],'Pull request source/target mismatch.')
    need(pull['head_sha']==task['candidate_sha'],'Pull request head differs from the recorded candidate.')
    return project,pull

def create_pull(repo,cfg,task,title,description):
    """Open this Story's pull request through the forge seam and return it normalized.

    v0.6 had no creation operation and delegated the step to `glab mr create` /
    `gh pr create`. That fails outright on a self-managed GitLab whose host is not the
    CLI's configured default, and the v0.6 pilot had to open both of its merge requests
    by hand in the web UI -- outside every check performed here.

    The branch must already be published at the exact candidate. Opening a request
    against a branch that has moved would bind the request to a commit no one reviewed,
    and the mismatch is far easier to read here than as a head SHA failure at merge.
    """
    full(task.get('candidate_sha'));need(text(task.get('branch')),'The task must record its source branch.')
    verify_remote(repo,cfg)
    published=remote_head(repo,cfg['remote'],task['branch'])
    need(published is not None,f'Publish {task["branch"]} at the candidate before opening the pull request.')
    need(published==task['candidate_sha'],'The published branch head differs from the recorded candidate; do not open a request against unreviewed work.')
    a=forge_adapter(cfg)
    pull=forge_call(cfg,a.create_pull_request,repo,cfg,task,title,description)
    need(pull['same_project'],'Cross-project/fork pull requests are unsupported.')
    need(pull['head_sha']==task['candidate_sha'],'Pull request head differs from the recorded candidate.')
    return pull

def forge_identity_check(repo,cfg,usernames):
    """Ask the forge which of these account names exist. Never an authorization check.

    Who may attest to a gate is decided by the project's own approved allow-lists, and
    that decision does not move here. This answers only whether a configured identity
    could ever act at all, so a Story nobody can satisfy is refused before it costs a
    build cycle instead of at the attestation step at the end of one.

    Returns {'verified','known','unknown','reason'}. `verified` is False whenever the
    forge could not be asked -- none configured, no CLI, no network, an error status --
    and `unknown` is then empty: "not asked" and "does not exist" are different facts,
    and only the second one may ever block a Story. Silence must not read as success.
    """
    names=sorted({x for x in usernames if text(x)})
    if not names:
        return {'verified':True,'known':[],'unknown':[],'reason':None}
    try:
        forge_ready(cfg);a=forge_adapter(cfg)
    except (AEError,OSError,ValueError,TypeError,KeyError) as e:
        return {'verified':False,'known':[],'unknown':[],'reason':str(e)}
    known=[];unknown=[]
    for name in names:
        try:
            exists=forge_call(cfg,a.user_exists,repo,cfg,name)
        except (AEError,OSError,ValueError,TypeError,KeyError) as e:
            return {'verified':False,'known':[],'unknown':[],'reason':f'{cfg["forge"]["provider"]} identity lookup did not answer: {e}'}
        (known if exists else unknown).append(name)
    return {'verified':True,'known':known,'unknown':unknown,'reason':None}

def forge_comment(repo,cfg,task,comment_id,*,verify_mr=True):
    """Read one exact comment on the configured pull request.

    A read-only server-backed identity primitive reused by merge approval and assurance
    attestation. It never turns a local boolean into human proof.
    """
    mr_task(task)
    need(type(comment_id) is int and comment_id>0,'A positive comment ID is required.')
    if verify_mr:
        project_pull(repo,cfg,task)
    a=forge_adapter(cfg)
    return forge_call(cfg,a.comment,repo,cfg,task,comment_id)

def verify_forge_comment(repo,cfg,task,note_id,expected_body,allowed_users,*,verify_mr=True):
    """Verify exact server-backed human evidence and return normalized provenance.

    `allowed_users` is supplied by the caller's semantics (merge approver, engineering
    owner, architecture/security allowlist, etc.). The helper verifies identity and exact
    comment content only; it does not infer authorization policy itself.

    The `system` check is what stops a machine-written comment standing in for a person.
    GitLab flags that directly; on GitHub the adapter derives it from the author account
    type, so a bot comment is refused there too.
    """
    need(text(expected_body) and '\n' not in expected_body and '\r' not in expected_body,'Expected comment body must be one exact non-placeholder line.')
    need(isinstance(allowed_users,list) and allowed_users and all(text(x) for x in allowed_users),'Allowed human identities must be configured.')
    note=forge_comment(repo,cfg,task,note_id,verify_mr=verify_mr)
    username=note.get('author')
    need(note.get('system') is False,'Machine-generated comments cannot satisfy human authority.')
    need(username in allowed_users,'Comment author is not authorized for this human action.')
    need((note.get('body') or '').strip()==expected_body,'Comment does not match the exact required attestation text.')
    created=note.get('created_at')
    observed=created if isinstance(created,str) and created.strip() else now()
    # Both refs are derived from the one the adapter spelled, so each provider keeps its
    # own vocabulary in exactly one place. GitLab still writes
    # `gitlab:mr:{iid}:note:{id}` as v0.5 did, byte for byte, so records written before
    # an upgrade still parse and compare; GitHub writes `github:pr:{n}:comment:{id}`.
    # Rebuilding either spelling here would have silently re-lettered GitLab's.
    attestor_ref=note.get('ref')
    need(text(attestor_ref) and ':user:' in attestor_ref,'Forge comment reference is missing or malformed.')
    return {
        'note_id':note_id,
        'username':username,
        'body':expected_body,
        'observed_at':observed,
        'attestor_ref':attestor_ref,
        'evidence_ref':'human-attestation:'+attestor_ref.rsplit(':user:',1)[0],
    }

def ancestor(repo,a,b):return git(repo,'merge-base','--is-ancestor',full(a),full(b),allowed=(0,1))[0]==0

def integration_proof(repo,task,pull,target):
    """Prove from Git that the candidate actually reached the target branch.

    Reads the normalized pull, not a provider's own field names, so the proof is the
    same for both forges. The two methods are exactly the ones v0.5 accepted: an
    ancestry proof where the candidate commit itself is in the target, and an exact
    blob/mode delta comparison where a squash replaced it.

    A provider that rewrites history on merge without reporting the result as a squash
    lands on the ancestry path and is refused. That is deliberate: the alternative is
    to assume a rewritten commit corresponds to the candidate, which is the one thing
    this function exists to check.
    """
    candidate=task['candidate_sha'];need(pull.get('state')=='merged' and pull.get('merged_at'),'MR is not verified merged.')
    need(pull.get('head_sha')==candidate,'Wrong candidate in merged MR.')
    merge=pull.get('merge_commit_sha');squash=pull.get('squash_commit_sha')
    if squash:
        need(ancestor(repo,squash,target),'Squash result not in target.')
        if merge:need(ancestor(repo,merge,target),'Merge result not in target.')
        base=full(pull.get('base_sha'));parent=git(repo,'rev-parse',squash+'^')[1]
        opts=('diff','--no-ext-diff','--no-textconv','--no-renames','--raw','--no-abbrev','-z')
        need(git(repo,*opts,base,candidate,'--')[1]==git(repo,*opts,parent,squash,'--')[1],'Squash delta is not an exact blob/mode match. Keep the branch for review.')
        return {'method':'squash_exact_delta','result_sha':squash,'target_sha':target}
    in_target=ancestor(repo,candidate,target)
    # The forge merged something, but not a descendant of the candidate: the history was
    # rewritten, and no field reports it as a squash whose delta could be compared
    # instead. Name that, rather than reporting a generic missing candidate.
    need(in_target or not merge,'The forge recorded a merge result that is not a descendant of the candidate, so the merge rewrote history and cannot be tied back to it. Keep the branch and review the target manually.')
    need(in_target,'Candidate not in target.')
    if merge:need(ancestor(repo,merge,target),'Recorded merge result not in target.')
    return {'method':'candidate_ancestry','result_sha':merge or candidate,'target_sha':target}

def task_path(repo,dev,story): return Path(repo)/'.agentic/local/tasks'/f'ae-{ident(dev)}-{ident(story)}.json'

def _relative_repo_ref(value, label):
    need(isinstance(value,str) and value and not value.startswith(('/', '\\')) and '..' not in Path(value).parts and '\\' not in value and ':' not in value, f'Invalid {label}.')
    return value

def _validate_schema3_gate_state(gate_id, state, contract_digest, candidate_sha):
    need(isinstance(gate_id,str) and isinstance(state,dict), 'Invalid assurance gate state.')
    required={'gate_id','stage','mode','required_sources','attestor_class','binding_class','status','binding','evidence_refs','attestor_ref','observed_at','revalidation','waiver_ref','residual_risk_refs'}
    need(set(state)==required, f'Assurance gate {gate_id} fields are invalid.')
    need(state.get('gate_id')==gate_id and state.get('stage')=='PRE_MERGE', 'Task pre_merge gate identity/stage mismatch.')
    need(state.get('attestor_class') in ('TOOL','INDEPENDENT_REVIEWER','ENGINEERING_OWNER','AUTHORIZED_HUMAN','COMPOSITE','CONTROL_PLANE'),'Unknown assurance attestor class.')
    need(state.get('binding_class') in ('CONTRACT','CANDIDATE'),'Story PRE_MERGE gate must use CONTRACT or CANDIDATE binding.')
    need(state.get('status') in ('PENDING','IN_PROGRESS','SATISFIED','FAILED','BLOCKED','NEEDS_REVALIDATION','WAIVED'),'Unknown assurance gate status.')
    need(isinstance(state.get('required_sources'),list) and state['required_sources'] and set(state['required_sources']).issubset({'project','tier','story'}),'Invalid assurance required_sources.')
    need(isinstance(state.get('evidence_refs'),list) and all(isinstance(x,str) and x and '\n' not in x and '\r' not in x for x in state['evidence_refs']) and len(state['evidence_refs'])==len(set(state['evidence_refs'])),'Invalid assurance evidence refs.')
    need(isinstance(state.get('residual_risk_refs'),list) and all(isinstance(x,str) and x and '\n' not in x and '\r' not in x for x in state['residual_risk_refs']) and len(state['residual_risk_refs'])==len(set(state['residual_risk_refs'])),'Invalid residual-risk refs.')
    need(state.get('attestor_ref') is None or (isinstance(state.get('attestor_ref'),str) and state['attestor_ref'].strip()),'Invalid assurance attestor_ref.')
    need(state.get('observed_at') is None or (isinstance(state.get('observed_at'),str) and state['observed_at'].strip()),'Invalid assurance observed_at.')
    b=state.get('binding');need(isinstance(b,dict) and set(b)=={'contract_digest','candidate_sha'},'Assurance binding fields are invalid.')
    need(b.get('contract_digest')==contract_digest,'Assurance gate contract digest mismatch.')
    if b.get('candidate_sha') is not None:full(b['candidate_sha'])
    if candidate_sha is None:
        need(b.get('candidate_sha') is None or state.get('status')=='NEEDS_REVALIDATION','Pre-candidate task cannot claim a bound candidate assurance state.')
    elif state.get('binding_class')=='CANDIDATE' and state.get('status') not in ('NEEDS_REVALIDATION',):
        need(b.get('candidate_sha')==candidate_sha,'Candidate-bound assurance state does not match task candidate.')
    rv=state.get('revalidation');need(isinstance(rv,dict) and set(rv)=={'disposition','from_binding','to_binding','assessor_class','assessor_ref'},'Revalidation fields are invalid.')
    need(rv.get('disposition') in ('NOT_EVALUATED','EQUIVALENT','NON_MATERIAL_TO_GATE','MATERIAL_TO_GATE','UNKNOWN'),'Invalid revalidation disposition.')
    status=state.get('status')
    if status=='SATISFIED':
        need(text(state.get('attestor_ref')) and text(state.get('observed_at')),'SATISFIED assurance requires attestor_ref and observed_at.')
        need(bool(state.get('evidence_refs')),'SATISFIED assurance requires evidence reference(s).')
        need(state.get('waiver_ref') is None,'SATISFIED assurance cannot carry waiver_ref.')
    if status=='WAIVED':
        need(text(state.get('waiver_ref')),'WAIVED assurance requires waiver_ref.')
    if status=='NEEDS_REVALIDATION':
        need(rv.get('from_binding') is not None and rv.get('to_binding') is not None,'NEEDS_REVALIDATION requires from/to binding evidence.')

def validate_task_schema2_legacy(task):
    """Validate the bounded subset needed to safely recognize an existing v0.4 task.

    This deliberately does not invent v0.5 contract/assurance fields. Lifecycle-specific
    evidence is still validated by the existing v0.4 quality/merge/cleanup paths.
    """
    need(isinstance(task,dict) and task.get('schema_version')==2 and task.get('workspace_model')=='REUSABLE_LANE_POOL',
         'Use an existing v0.4 reusable-pool task schema 2 for legacy compatibility.')
    ident(task.get('task_id'));ident(task.get('developer'));full(task.get('base_sha'))
    need(task.get('branch')==f'work/{task["developer"]}/{task["task_id"]}','Wrong owner/Story source branch.')
    if task.get('candidate_sha') is not None: full(task['candidate_sha'])
    need(type(task.get('risk_tier')) is int and task['risk_tier'] in (1,2,3),'Legacy task risk_tier must be 1, 2, or 3.')
    need(task.get('merge_mode_override') in (None,'DEVELOPER_REVIEW'),'Legacy task merge override may only narrow to DEVELOPER_REVIEW.')
    need(isinstance(task.get('allocations'),list),'Legacy task allocations must be a list.')
    need(task.get('mr_iid') is None or (type(task.get('mr_iid')) is int and task['mr_iid']>0),'Legacy MR IID must be null or a positive integer.')
    return task

def legacy_effective_additional_assurance(cfg,task):
    """Resolve only project/Tier *additional* assurance applicable to a schema-2 Story.

    `technical_review` is excluded because v0.4 already has its exact-candidate technical
    review semantics. No Story-specific obligations are synthesized for legacy work.
    """
    validate_task_schema2_legacy(task)
    try:
        import contract as _contract
    except ModuleNotFoundError:  # pragma: no cover
        from . import contract as _contract
    policy=cfg.get('assurance_policy')
    _contract.validate_assurance_policy(policy,require_approved=True)
    resolved=_contract.resolve_effective_assurance(policy,task['risk_tier'],[])
    return [x for x in resolved if x.get('gate_id')!='technical_review']

def validate_legacy_task_compatibility(cfg,task):
    """Fail closed unless a schema-2 Story can continue exactly under v0.4 semantics."""
    extra=legacy_effective_additional_assurance(cfg,task)
    if extra:
        details=', '.join(f"{x['gate_id']}@{x['stage']}" for x in extra)
        raise AEError('Legacy schema-2 Story cannot continue under v0.4 semantics because current project/Tier assurance adds: '+details+'. Explicitly bind a normalized Story contract and upgrade this task to schema 3 before further execution/agent merge.')
    return {
        'result':'LEGACY_V04_COMPATIBLE',
        'contract_status':'LEGACY_V04_CONTRACT',
        'task_schema_version':2,
        'effective_additional_assurance':[],
        'notice':'Compatibility mode preserves v0.4 task/evidence semantics. No normalized contract or assurance PASS was synthesized.',
    }

def validate_task_schema3(task):
    need(task.get('schema_version')==3 and task.get('workspace_model')=='REUSABLE_LANE_POOL','Use task manifest schema 3 for v0.5 tasks.')
    ident(task.get('task_id'));ident(task.get('developer'));full(task.get('base_sha'))
    need(task.get('branch')==f'work/{task["developer"]}/{task["task_id"]}','Wrong owner/Story source branch.')
    if task.get('candidate_sha') is not None:full(task['candidate_sha'])
    need(type(task.get('risk_tier')) is int and task['risk_tier'] in (1,2,3),'Task risk_tier must be 1, 2, or 3.')
    need(task.get('merge_mode_override') in (None,'DEVELOPER_REVIEW'),'Task merge override may only narrow to DEVELOPER_REVIEW.')
    need(isinstance(task.get('allocations'),list),'Task allocations must be a list.')
    need(task.get('mr_iid') is None or (type(task.get('mr_iid')) is int and task['mr_iid']>0),'MR IID must be null or a positive integer.')

    c=task.get('contract');need(isinstance(c,dict),'Task schema 3 requires contract snapshot.')
    ckeys={'schema_version','contract_ref','contract_digest','canonical_backlog_ref','backlog_revision','authority_publication_sha','story_ref','traceability_root','ac_ids','engineering_owner','material_impact','capability_id','final_acceptance_ref','evidence_expectations'}
    need(set(c)==ckeys and c.get('schema_version')==2,'Task contract snapshot schema is invalid.')
    _relative_repo_ref(c.get('contract_ref'),'contract_ref');_relative_repo_ref(c.get('canonical_backlog_ref'),'canonical_backlog_ref');_relative_repo_ref(c.get('story_ref'),'story_ref')
    need(re.fullmatch(r'[0-9a-f]{64}',str(c.get('contract_digest',''))) is not None,'Task contract_digest must be SHA-256 hex.')
    full(c.get('authority_publication_sha'))
    need(isinstance(c.get('backlog_revision'),str) and c['backlog_revision'].strip(),'Task backlog revision required.')
    need(isinstance(c.get('traceability_root'),list) and c['traceability_root'] and all(isinstance(x,str) and x for x in c['traceability_root']),'Task traceability roots required.')
    need(isinstance(c.get('ac_ids'),list) and c['ac_ids'] and all(isinstance(x,str) and x for x in c['ac_ids']),'Task AC IDs required.')
    need(c.get('engineering_owner') is None or isinstance(c.get('engineering_owner'),dict),'Task engineering owner snapshot invalid.')
    allowed_impact={'architecture','scalability_nfr','security_privacy_data','operability_recovery','human_ownership','traceability_audit'}
    need(isinstance(c.get('material_impact'),list) and set(c['material_impact']).issubset(allowed_impact) and len(c['material_impact'])==len(set(c['material_impact'])),'Task material impact summary invalid.')
    need(c.get('capability_id') is None or isinstance(c.get('capability_id'),str),'Task capability ID invalid.')
    need(isinstance(c.get('final_acceptance_ref'),str) and c['final_acceptance_ref'],'Task final acceptance ref required.')

    assurance=task.get('assurance');need(isinstance(assurance,dict) and set(assurance)=={'schema_version','policy_snapshot','pre_merge','downstream_obligations'} and assurance.get('schema_version')==1,'Task assurance block invalid.')
    ps=assurance.get('policy_snapshot');need(isinstance(ps,dict) and set(ps)=={'approval_ref','resolved_at','project_required','story_required'},'Assurance policy snapshot invalid.')
    need(text(ps.get('approval_ref')),'Assurance policy approval ref required.');fresh(ps.get('resolved_at'),seconds=315360000)
    for key in ('project_required','story_required'):
        need(isinstance(ps.get(key),list) and all(isinstance(x,str) and x for x in ps[key]),f'Assurance policy {key} invalid.')
    pre=assurance.get('pre_merge');need(isinstance(pre,dict),'Assurance pre_merge must be an object.')
    need('technical_review' not in pre,'technical_review remains virtual and must not be duplicated in assurance.pre_merge.')
    for gate_id,state in pre.items():_validate_schema3_gate_state(gate_id,state,c['contract_digest'],task.get('candidate_sha'))
    downstream=assurance.get('downstream_obligations');need(isinstance(downstream,list),'downstream_obligations must be a list.')
    for item in downstream:
        need(isinstance(item,dict) and set(item)=={'gate_id','stage','mode','capability_id','required_sources','evidence_plan_refs','registry_ref'},'Downstream obligation fields are invalid.')
        need(item.get('stage') in ('POST_INTEGRATION','RELEASE_POINTER'),'Downstream obligation has invalid stage.')
        need(isinstance(item.get('required_sources'),list) and item['required_sources'],'Downstream required_sources missing.')
        need(isinstance(item.get('evidence_plan_refs'),list),'Downstream evidence plan refs invalid.')
    tr=task.get('traceability');need(isinstance(tr,dict) and set(tr)=={'record_ref','capability_record_ref','release_pointer_refs'},'Task traceability block invalid.')
    expected_trace=traceability_record_ref(task)
    need(tr.get('record_ref') in (None,expected_trace),'Task traceability record_ref must be the exact registered Story record path.')
    if tr.get('capability_record_ref') is not None:_relative_repo_ref(tr.get('capability_record_ref'),'capability_record_ref')
    need(isinstance(tr.get('release_pointer_refs'),list) and all(isinstance(x,str) and x.strip() and '\n' not in x and '\r' not in x for x in tr['release_pointer_refs']) and len(tr['release_pointer_refs'])==len(set(tr['release_pointer_refs'])),'release_pointer_refs must be a unique string list.')
    return task


def traceability_record_ref(task):
    """Stable team-readable Story traceability index path."""
    ident(task.get('developer'));ident(task.get('task_id'))
    return f"docs/agentic/records/{task['developer']}/{task['task_id']}/traceability.json"


def _traceability_assurance_index(task, assurance_state=None):
    pre=[]
    for gate_id,gate in sorted(task.get('assurance',{}).get('pre_merge',{}).items()):
        pre.append({
            'gate_id':gate_id,
            'stage':gate.get('stage'),
            'mode':gate.get('mode'),
            'required_sources':list(gate.get('required_sources',[])),
            'status':gate.get('status'),
            'binding_class':gate.get('binding_class'),
            'binding':dict(gate.get('binding') or {}),
            'evidence_refs':list(gate.get('evidence_refs',[])),
            'attestor_ref':gate.get('attestor_ref'),
            'observed_at':gate.get('observed_at'),
            'revalidation':dict(gate.get('revalidation') or {}),
            'waiver_id':None,
            'waiver_approval_ref':gate.get('attestor_ref') if gate.get('status')=='WAIVED' else None,
            'residual_risk_refs':list(gate.get('residual_risk_refs',[])),
        })
    # Enrich valid WAIVED entries with a stable waiver identifier while avoiding
    # publication of local-only .agentic/local paths.
    if isinstance(assurance_state,dict):
        by_gate={x.get('gate_id'):x for x in assurance_state.get('waived_exceptions',[]) if isinstance(x,dict)}
        for item in pre:
            ex=by_gate.get(item['gate_id'])
            if ex:
                item['waiver_id']=ex.get('waiver_id')
                item['waiver_approval_ref']=ex.get('risk_owner') and item.get('attestor_ref')
    downstream=[]
    for item in task.get('assurance',{}).get('downstream_obligations',[]):
        downstream.append({
            'gate_id':item.get('gate_id'),
            'stage':item.get('stage'),
            'mode':item.get('mode'),
            'capability_id':item.get('capability_id'),
            'required_sources':list(item.get('required_sources',[])),
            'evidence_plan_refs':list(item.get('evidence_plan_refs',[])),
            'registry_ref':item.get('registry_ref'),
        })
    return {
        'validation_result':None if assurance_state is None else assurance_state.get('result'),
        'validation_error':None if assurance_state is None else assurance_state.get('error'),
        'pre_merge':pre,
        'downstream_obligations':downstream,
    }


def build_traceability_index(repo,cfg,task,mr,proof,assurance_state=None):
    """Build the versionable Story traceability index. This is an index + pointers, not an evidence warehouse."""
    repo=root(repo);validate_task_schema3(task);mr_task(task)
    need(isinstance(mr,dict) and mr.get('state')=='merged' and text(mr.get('merged_at')),'Traceability requires a verified merged MR.')
    need(mr.get('head_sha')==task.get('candidate_sha'),'Traceability MR head differs from candidate.')
    need(isinstance(proof,dict) and proof.get('result_sha') and proof.get('target_sha'),'Integration proof required for traceability.')
    full(proof['result_sha']);full(proof['target_sha'])
    c=task['contract'];review=task.get('review') or {}
    need(review.get('verdict')=='ACCEPT' and review.get('candidate_sha')==task.get('candidate_sha') and text(review.get('report_ref')),'Traceability requires exact technical review provenance.')
    owner=c.get('engineering_owner')
    if owner is not None: need(isinstance(owner,dict),'Traceability engineering owner snapshot invalid.')
    # Traceability records the forge that produced the merge request, so a record stays
    # readable after a team migrates between providers.
    _f=cfg['forge'];mr_ref=f"{_f['provider']}:{_f['repo']}!{task['mr_iid']}"
    record={
        'schema_version':1,
        'record_type':'STORY_TRACEABILITY',
        'integration_observed_at':mr.get('merged_at'),
        'traceability_root':list(c['traceability_root']),
        'authority':{
            'canonical_backlog':{
                'path':c['canonical_backlog_ref'],
                'revision':c['backlog_revision'],
                'publication_sha':c['authority_publication_sha'],
            },
            'story_card':{'path':c['story_ref']},
            'contract':{'digest':c['contract_digest']},
        },
        'story':{
            'story_id':task['task_id'],
            'developer':task['developer'],
            'branch':task['branch'],
            'risk_tier':task['risk_tier'],
            'ac_ids':list(c['ac_ids']),
            'engineering_owner':owner,
            'material_engineering_impact':list(c['material_impact']),
            'capability_id':c.get('capability_id'),
            'final_acceptance_ref':c.get('final_acceptance_ref'),
        },
        'candidate_sha':task['candidate_sha'],
        'technical_review':{
            'verdict':review.get('verdict'),
            'candidate_sha':review.get('candidate_sha'),
            'report_ref':review.get('report_ref'),
            'invocation_id':review.get('invocation_id'),
            'lane_id':review.get('lane_id'),
        },
        'assurance':_traceability_assurance_index(task,assurance_state),
        'mr':{
            'iid':task['mr_iid'],
            'provider':_f['provider'],
            'project':_f['repo'],
            'ref':mr_ref,
            'source_branch':task['branch'],
            'target_branch':cfg['target_branch'],
        },
        'merge':{
            'method':proof.get('method'),
            'result_sha':proof.get('result_sha'),
            'target_sha':proof.get('target_sha'),
            'ref':'git:'+proof.get('result_sha'),
        },
        'downstream':{
            'capability_record_ref':task.get('traceability',{}).get('capability_record_ref'),
            'release_pointer_refs':list(task.get('traceability',{}).get('release_pointer_refs',[])),
        },
        'build_complete_eligible':False,
        'notice':'Traceability index only. Integration/assurance evidence is referenced, not copied. Merge does not imply BUILD COMPLETE, deployment, or release.',
    }
    return validate_traceability_index(record)


def build_legacy_traceability_index(repo,cfg,task,mr,proof,compatibility=None):
    """Build a factual integration index for an unbound v0.4 schema-2 Story.

    The record is intentionally incomplete versus schema-3 traceability and says so
    explicitly. It never fabricates requirement/AC/contract authority that did not exist.
    """
    repo=root(repo);validate_task_schema2_legacy(task);mr_task(task)
    compatibility=compatibility or validate_legacy_task_compatibility(cfg,task)
    need(compatibility.get('contract_status')=='LEGACY_V04_CONTRACT','Legacy compatibility marker missing.')
    need(isinstance(mr,dict) and mr.get('state')=='merged' and text(mr.get('merged_at')),'Legacy traceability requires a verified merged MR.')
    need(mr.get('head_sha')==task.get('candidate_sha'),'Legacy traceability MR head differs from candidate.')
    need(isinstance(proof,dict) and proof.get('result_sha') and proof.get('target_sha'),'Integration proof required for legacy traceability.')
    full(proof['result_sha']);full(proof['target_sha'])
    review=task.get('review') or {}
    need(review.get('verdict')=='ACCEPT' and review.get('candidate_sha')==task.get('candidate_sha') and text(review.get('report_ref')),'Legacy traceability requires exact v0.4 technical review provenance.')
    record={
        'schema_version':1,
        'record_type':'LEGACY_V04_TRACEABILITY',
        'integration_observed_at':mr.get('merged_at'),
        'contract_status':'LEGACY_V04_CONTRACT',
        'story':{
            'story_id':task['task_id'],
            'developer':task['developer'],
            'branch':task['branch'],
            'risk_tier':task['risk_tier'],
            'source_task_schema':2,
        },
        'candidate_sha':task['candidate_sha'],
        'technical_review':{
            'verdict':review.get('verdict'),
            'candidate_sha':review.get('candidate_sha'),
            'report_ref':review.get('report_ref'),
            'invocation_id':review.get('invocation_id'),
            'lane_id':review.get('lane_id'),
        },
        'assurance':{
            'compatibility_mode':'V04_SEMANTICS_ONLY',
            'effective_additional_assurance':[
                {'gate_id':x.get('gate_id'),'stage':x.get('stage'),'mode':x.get('mode'),'required_sources':list(x.get('required_sources',[]))}
                for x in compatibility.get('effective_additional_assurance',[])
            ],
            'claim':'NO_V05_ASSURANCE_SYNTHESIZED' if not compatibility.get('effective_additional_assurance') else 'ASSURANCE_INCOMPLETE_AFTER_INTEGRATION',
        },
        'mr':{
            'iid':task['mr_iid'],
            'provider':cfg['forge']['provider'],
            'project':cfg['forge']['repo'],
            'ref':f"{cfg['forge']['provider']}:{cfg['forge']['repo']}!{task['mr_iid']}",
            'source_branch':task['branch'],
            'target_branch':cfg['target_branch'],
        },
        'merge':{
            'method':proof.get('method'),
            'result_sha':proof.get('result_sha'),
            'target_sha':proof.get('target_sha'),
            'ref':'git:'+proof.get('result_sha'),
        },
        'build_complete_eligible':False,
        'notice':'LEGACY_V04_CONTRACT: no normalized Story contract, v0.5 requirement/AC traceability, or additional assurance was synthesized. This record preserves only factual v0.4 integration provenance and does not imply BUILD COMPLETE, deployment, or release.',
    }
    return validate_legacy_traceability_index(record)

def validate_legacy_traceability_index(record):
    required={'schema_version','record_type','integration_observed_at','contract_status','story','candidate_sha','technical_review','assurance','mr','merge','build_complete_eligible','notice'}
    need(isinstance(record,dict) and set(record)==required and record.get('schema_version')==1 and record.get('record_type')=='LEGACY_V04_TRACEABILITY','Legacy traceability schema 1 fields are invalid.')
    need(record.get('contract_status')=='LEGACY_V04_CONTRACT','Legacy traceability contract marker invalid.')
    need(text(record.get('integration_observed_at')),'Legacy traceability integration time required.')
    st=record.get('story');need(isinstance(st,dict) and set(st)=={'story_id','developer','branch','risk_tier','source_task_schema'},'Legacy traceability Story block invalid.')
    ident(st.get('story_id'));ident(st.get('developer'));need(st.get('branch')==f"work/{st['developer']}/{st['story_id']}",'Legacy traceability Story branch invalid.');need(type(st.get('risk_tier')) is int and st['risk_tier'] in (1,2,3) and st.get('source_task_schema')==2,'Legacy traceability risk/schema invalid.')
    full(record.get('candidate_sha'))
    rv=record.get('technical_review');need(isinstance(rv,dict) and set(rv)=={'verdict','candidate_sha','report_ref','invocation_id','lane_id'},'Legacy traceability review block invalid.');need(rv.get('verdict')=='ACCEPT' and rv.get('candidate_sha')==record['candidate_sha'] and text(rv.get('report_ref')),'Legacy traceability technical review invalid.')
    ass=record.get('assurance');need(isinstance(ass,dict) and set(ass)=={'compatibility_mode','effective_additional_assurance','claim'},'Legacy traceability assurance block invalid.');need(ass.get('compatibility_mode')=='V04_SEMANTICS_ONLY' and isinstance(ass.get('effective_additional_assurance'),list),'Legacy traceability assurance compatibility mode invalid.')
    for item in ass['effective_additional_assurance']:
        need(isinstance(item,dict) and set(item)=={'gate_id','stage','mode','required_sources'} and text(item.get('gate_id')) and item.get('stage') in ('PRE_MERGE','POST_INTEGRATION','RELEASE_POINTER') and isinstance(item.get('required_sources'),list),'Legacy traceability additional assurance entry invalid.')
    expected_claim='NO_V05_ASSURANCE_SYNTHESIZED' if not ass['effective_additional_assurance'] else 'ASSURANCE_INCOMPLETE_AFTER_INTEGRATION'
    need(ass.get('claim')==expected_claim,'Legacy traceability assurance claim does not match current obligations.')
    m=record.get('mr');need(isinstance(m,dict) and set(m)=={'iid','provider','project','ref','source_branch','target_branch'},'Legacy traceability MR block invalid.');need(type(m.get('iid')) is int and m['iid']>0 and m.get('provider') in FORGE_PROVIDERS and text(m.get('project')) and text(m.get('ref')),'Legacy traceability MR identity invalid.')
    mg=record.get('merge');need(isinstance(mg,dict) and set(mg)=={'method','result_sha','target_sha','ref'},'Legacy traceability merge block invalid.');need(mg.get('method') in ('candidate_ancestry','squash_exact_delta') and text(mg.get('ref')),'Legacy traceability merge method/ref invalid.');full(mg.get('result_sha'));full(mg.get('target_sha'))
    need(record.get('build_complete_eligible') is False,'Legacy v0.4 integration cannot claim v0.5 BUILD COMPLETE eligibility.')
    return record

def validate_traceability_index(record):
    if isinstance(record,dict) and record.get('record_type')=='LEGACY_V04_TRACEABILITY':
        return validate_legacy_traceability_index(record)
    required={'schema_version','record_type','integration_observed_at','traceability_root','authority','story','candidate_sha','technical_review','assurance','mr','merge','downstream','build_complete_eligible','notice'}
    need(isinstance(record,dict) and set(record)==required and record.get('schema_version')==1 and record.get('record_type')=='STORY_TRACEABILITY','Traceability schema 1 fields are invalid.')
    need(text(record.get('integration_observed_at')),'Traceability integration time required.')
    need(isinstance(record.get('traceability_root'),list) and record['traceability_root'] and all(text(x) for x in record['traceability_root']),'Traceability roots invalid.')
    a=record.get('authority');need(isinstance(a,dict) and set(a)=={'canonical_backlog','story_card','contract'},'Traceability authority block invalid.')
    cb=a['canonical_backlog'];need(isinstance(cb,dict) and set(cb)=={'path','revision','publication_sha'},'Traceability canonical backlog block invalid.');_relative_repo_ref(cb.get('path'),'traceability backlog path');need(text(cb.get('revision')),'Traceability backlog revision required.');full(cb.get('publication_sha'))
    sc=a['story_card'];need(isinstance(sc,dict) and set(sc)=={'path'},'Traceability story-card block invalid.');_relative_repo_ref(sc.get('path'),'traceability Story path')
    ct=a['contract'];need(isinstance(ct,dict) and set(ct)=={'digest'},'Traceability contract block invalid.');need(re.fullmatch(r'[0-9a-f]{64}',str(ct.get('digest',''))) is not None,'Traceability contract digest invalid.')
    st=record.get('story');need(isinstance(st,dict) and set(st)=={'story_id','developer','branch','risk_tier','ac_ids','engineering_owner','material_engineering_impact','capability_id','final_acceptance_ref'},'Traceability Story block invalid.');ident(st.get('story_id'));ident(st.get('developer'));need(st.get('branch')==f"work/{st['developer']}/{st['story_id']}",'Traceability Story branch invalid.');need(type(st.get('risk_tier')) is int and st['risk_tier'] in (1,2,3),'Traceability risk Tier invalid.');need(isinstance(st.get('ac_ids'),list) and st['ac_ids'] and all(text(x) for x in st['ac_ids']),'Traceability AC IDs invalid.');need(st.get('engineering_owner') is None or isinstance(st.get('engineering_owner'),dict),'Traceability engineering owner invalid.');need(isinstance(st.get('material_engineering_impact'),list),'Traceability impact list invalid.');need(st.get('capability_id') is None or text(st.get('capability_id')),'Traceability capability ID invalid.');need(text(st.get('final_acceptance_ref')),'Traceability final acceptance ref required.')
    full(record.get('candidate_sha'))
    rv=record.get('technical_review');need(isinstance(rv,dict) and set(rv)=={'verdict','candidate_sha','report_ref','invocation_id','lane_id'},'Traceability review block invalid.');need(rv.get('verdict')=='ACCEPT' and rv.get('candidate_sha')==record['candidate_sha'] and text(rv.get('report_ref')),'Traceability technical review invalid.')
    ass=record.get('assurance');need(isinstance(ass,dict) and set(ass)=={'validation_result','validation_error','pre_merge','downstream_obligations'},'Traceability assurance block invalid.');need(isinstance(ass.get('pre_merge'),list) and isinstance(ass.get('downstream_obligations'),list),'Traceability assurance lists invalid.')
    for g in ass['pre_merge']:
        need(isinstance(g,dict) and set(g)=={'gate_id','stage','mode','required_sources','status','binding_class','binding','evidence_refs','attestor_ref','observed_at','revalidation','waiver_id','waiver_approval_ref','residual_risk_refs'},'Traceability assurance gate entry invalid.');need(text(g.get('gate_id')) and g.get('stage')=='PRE_MERGE','Traceability assurance gate identity invalid.');need(g.get('status') in ('PENDING','IN_PROGRESS','SATISFIED','FAILED','BLOCKED','NEEDS_REVALIDATION','WAIVED'),'Traceability assurance gate status invalid.');need(isinstance(g.get('evidence_refs'),list) and isinstance(g.get('residual_risk_refs'),list),'Traceability assurance refs invalid.')
    m=record.get('mr');need(isinstance(m,dict) and set(m)=={'iid','provider','project','ref','source_branch','target_branch'},'Traceability MR block invalid.');need(type(m.get('iid')) is int and m['iid']>0 and m.get('provider') in FORGE_PROVIDERS and text(m.get('project')) and text(m.get('ref')),'Traceability MR identity invalid.')
    mg=record.get('merge');need(isinstance(mg,dict) and set(mg)=={'method','result_sha','target_sha','ref'},'Traceability merge block invalid.');need(mg.get('method') in ('candidate_ancestry','squash_exact_delta') and text(mg.get('ref')),'Traceability merge method/ref invalid.');full(mg.get('result_sha'));full(mg.get('target_sha'))
    ds=record.get('downstream');need(isinstance(ds,dict) and set(ds)=={'capability_record_ref','release_pointer_refs'},'Traceability downstream block invalid.');need(ds.get('capability_record_ref') is None or text(ds.get('capability_record_ref')),'Traceability capability record ref invalid.');need(isinstance(ds.get('release_pointer_refs'),list) and all(text(x) for x in ds['release_pointer_refs']),'Traceability release pointers invalid.')
    need(record.get('build_complete_eligible') is False,'Story merge traceability must not claim BUILD COMPLETE eligibility.')
    return record


def write_traceability_index(repo,cfg,task,mr,proof,assurance_state=None,*,apply=False):
    """Write/update the Story index in the coordinator records tree and bind its ref into local task state."""
    repo=require_coordinator(repo,task.get('developer'));record=build_traceability_index(repo,cfg,task,mr,proof,assurance_state)
    rel=traceability_record_ref(task);path=(Path(repo)/rel).absolute();ensure_no_links(path.parent if path.parent.exists() else path.parent.parent,repo)
    if path.exists():
        old=read_json(path);validate_traceability_index(old)
        immutable=lambda x:(x['story']['story_id'],x['story']['developer'],x['authority']['contract']['digest'],x['candidate_sha'],x['mr']['iid'],x['merge']['result_sha'])
        need(immutable(old)==immutable(record),'Existing traceability record belongs to different immutable Story/integration facts; do not overwrite it.')
        if old==record:
            if task.get('traceability',{}).get('record_ref')!=rel and apply:
                task['traceability']['record_ref']=rel;atomic_json(task_path(repo,task['developer'],task['task_id']),task)
            return {'result':'TRACEABILITY_CURRENT','record_ref':rel,'publication_required':True,'record':old}
    if apply:
        path.parent.mkdir(parents=True,exist_ok=True);ensure_no_links(path.parent,repo);atomic_json(path,record)
        task['traceability']['record_ref']=rel;atomic_json(task_path(repo,task['developer'],task['task_id']),task)
        return {'result':'TRACEABILITY_WRITTEN','record_ref':rel,'publication_required':True,'record':record}
    return {'result':'TRACEABILITY_PREVIEW','record_ref':rel,'publication_required':True,'record':record}

def write_legacy_traceability_index(repo,cfg,task,mr,proof,compatibility=None,*,apply=False):
    """Write a versioned factual marker for a compatible schema-2 integration."""
    repo=require_coordinator(repo,task.get('developer'))
    record=build_legacy_traceability_index(repo,cfg,task,mr,proof,compatibility)
    rel=traceability_record_ref(task);path=(Path(repo)/rel).absolute();ensure_no_links(path.parent if path.parent.exists() else path.parent.parent,repo)
    if path.exists():
        old=read_json(path);validate_traceability_index(old)
        need(old.get('record_type')=='LEGACY_V04_TRACEABILITY','Existing traceability record is schema-3 authority; do not replace it with a legacy marker.')
        immutable=lambda x:(x['story']['story_id'],x['story']['developer'],x['candidate_sha'],x['mr']['iid'],x['merge']['result_sha'])
        need(immutable(old)==immutable(record),'Existing legacy traceability record belongs to different immutable integration facts; do not overwrite it.')
        if old==record:return {'result':'LEGACY_TRACEABILITY_CURRENT','record_ref':rel,'publication_required':True,'record':old}
    if apply:
        path.parent.mkdir(parents=True,exist_ok=True);ensure_no_links(path.parent,repo);atomic_json(path,record)
        return {'result':'LEGACY_TRACEABILITY_WRITTEN','record_ref':rel,'publication_required':True,'record':record}
    return {'result':'LEGACY_TRACEABILITY_PREVIEW','record_ref':rel,'publication_required':True,'record':record}

def load_task(repo,path):
    p=Path(path);p=(Path(repo)/p).absolute() if not p.is_absolute() else p
    ensure_no_links(p,Path(repo));task=read_json(p)
    need(p.resolve()==task_path(repo,task.get('developer'),task.get('task_id')).resolve(),'Use the exact registered task manifest.')
    schema=task.get('schema_version')
    if schema==3:
        validate_task_schema3(task)
    elif schema==2:
        validate_task_schema2_legacy(task)
    else:
        raise AEError('Unknown task manifest schema; preserve it and follow migration guidance.')
    require_coordinator(repo,task['developer'])
    return p,task

def mr_task(task):
    full(task.get('candidate_sha'));need(type(task.get('mr_iid'))==int and task['mr_iid']>0,'Exact MR IID required.')

def cli_error(exc):
    print(json.dumps({'result':'RECOVERY_REQUIRED','message':str(exc),'notice':'An action may have partly completed. Re-read Git, MR and lease state before retrying.'},indent=2));return 2

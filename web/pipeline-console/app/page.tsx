'use client';
import {useEffect,useRef,useState} from 'react';
import RunForm from '../components/RunForm';
import RunDetail from '../components/RunDetail';
import {api,commandId,display,establishSession,Value} from '../lib/api';
import {newInputs,resumeInputs,passing,busy,outcome,changedInput,canDispatch,legacyDisposition} from '../lib/contract.mjs';

export default function Console(){
  const [page,setPage]=useState('dashboard'),[runs,setRuns]=useState<Value[]>([]),[operations,setOperations]=useState<Value[]>([]);
  const [legacy,setLegacy]=useState<string[]>([]),[detail,setDetail]=useState<Value|null>(null),[logs,setLogs]=useState<Value[]>([]);
  const [selected,setSelected]=useState<string|null>(null),[inputs,setInputs]=useState<Value>(newInputs()),[preflight,setPreflight]=useState<Value|null>(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[ready,setReady]=useState(false),[sending,setSending]=useState(false);
  const [execution,setExecution]=useState<Value>({max_runs:1,occupied:0,available:0,blocked:true});
  const [availability,setAvailability]=useState(''),[before,setBefore]=useState<string|null>(null),[next,setNext]=useState<string|null>(null);
  const flight=useRef(false),boot=useRef<Promise<void>|null>(null);
  const command=async(action:()=>Promise<void>)=>{if(flight.current)return;flight.current=true;setSending(true);setError('');
    try{await action();}catch(reason){setError(String(reason));}finally{flight.current=false;setSending(false);}};
  useEffect(()=>{let active=true;boot.current ||= establishSession();boot.current.then(()=>{if(active)setReady(true);}).catch(reason=>{if(active)setError(String(reason));});return()=>{active=false;};},[]);
  useEffect(()=>{
    if(!ready)return;let active=true;let timer:ReturnType<typeof setTimeout>;
    const refresh=async()=>{try{
      const [history,jobs]=await Promise.all([api('/runs'+(before?'?before='+encodeURIComponent(before):'')),api('/operations')]);
      if(active){setRuns(history.items);setNext(history.next_cursor);setAvailability(history.availability);setOperations(jobs.items);setLegacy(jobs.legacy_running);setExecution(jobs.execution);}
      if(selected){const [value,events]=await Promise.all([api('/runs/'+encodeURIComponent(selected)),api('/runs/'+encodeURIComponent(selected)+'/logs')]);if(active){setDetail(value);setLogs(events.items);}}
    }catch(reason){if(active)setError('Connection/data unavailable: '+String(reason));}finally{if(active)timer=setTimeout(refresh,2500);}};
    refresh();return()=>{active=false;clearTimeout(timer);};
  },[ready,selected,before]);
  useEffect(()=>{if(preflight){const latest=operations.find(row=>row.operation_id===preflight.operation_id);if(latest)setPreflight(latest);}},[operations]);
  const current=detail?.console_operations?.find((row:Value)=>row.controls_available);
  const locked=sending||!canDispatch(execution,legacy,inputs.action==='rebuild_tiles')||!ready;
  const newRun=()=>{setInputs(newInputs());setPreflight(null);setPage('new');setNotice('');};
  const openRun=(runId:string)=>{setSelected(runId);setDetail(null);setLogs([]);setPage('detail');setNotice('');};
  const update=(field:string,value:any)=>{setInputs(changedInput(inputs,field,value).inputs);setPreflight(null);setNotice('');};
  const check=()=>command(async()=>{const value=await api('/preflight',{command_id:commandId(),inputs});setPreflight(value);setOperations(old=>[value,...old]);});
  const start=()=>command(async()=>{if(!passing(preflight))return;const value=await api('/start',{command_id:commandId(),preflight_id:preflight!.operation_id});setOperations(old=>[value,...old]);setPreflight(null);setPage('dashboard');setNotice('Request recorded. The runner rechecks storage and inputs before execution.');});
  const control=(action:string)=>command(async()=>{if(!current||!confirm(`${action==='pause'?'Pause':'Stop'} local pipeline? WebODM is not canceled. A busy GDAL operation may finish first.`))return;
    await api(`/operations/${current.operation_id}/controls`,{command_id:commandId(),action});setNotice(action+' requested — waiting for a safe control checkpoint.');});
  const decide=(action:string,extra:Value={})=>command(async()=>{if(!current?.review)return;
    await api(`/operations/${current.operation_id}/review`,{command_id:commandId(),review_id:current.review.review_id,action,...extra});setNotice('Review decision submitted.');});
  return <div className="workspace"><aside className="rail"><div className="brand"><span className="brand-mark">V</span><div>Pipeline Console<small>LOCAL OPERATOR WORKSPACE</small></div></div><nav><button className={page==='dashboard'?'selected':''} onClick={()=>setPage('dashboard')}>Overview & runs</button><button className={page==='new'?'selected':''} onClick={newRun}>New run</button>{selected&&<button className={page==='detail'?'selected':''} onClick={()=>setPage('detail')}>Run detail</button>}</nav><div className="rail-bottom"><span className="dot"/>This workstation only<small>{execution.max_runs} local slot(s) · no queue<br/>Remote tasks may continue while paused.</small></div></aside>
    <main><header><div><p className="eyebrow">SURVEY PROCESSING</p><h1>{page==='dashboard'?'Pipeline overview':page==='detail'?'Run workspace':inputs.action==='new'?'Prepare a new run':inputs.action==='resume'?'Resume processing':'Rebuild QGIS tiles'}</h1></div><div className="local-badge">127.0.0.1 <span>●</span></div></header>
      {error&&<div role="alert" className="alert error">{error}<button onClick={()=>setError('')}>Dismiss</button></div>}{notice&&<div role="status" className="alert">{notice}</div>}
      {!!legacy.length&&<div className="alert warning">Terminal run(s) are active or unverified: {legacy.join(', ')}. Monitoring only until they stop or pause; no live takeover.</div>}
      {!!execution.legacy_reconciled?.length&&<div role="status" className="alert">{execution.legacy_reconciled.length} historical run(s) reconciled locally. Persisted statuses remain unchanged; WebODM state is unverified.</div>}
      {page==='dashboard'&&<><section className="metrics"><article><small>Local execution slots</small><strong>{execution.occupied} / {execution.max_runs} occupied</strong><p>Single-host ownership guard</p></article><article><small>Runs in this view</small><strong>{runs.length}</strong><p>Persisted pipeline records</p></article><article><small>WebODM</small><strong>Separate lifecycle</strong><p>Status is last observed, not a live probe</p></article></section>
        {[...operations.filter(busy),...operations.filter(row=>!busy(row)).slice(0,4)].map(row=><section className="operation" key={row.operation_id}><div><span className="badge">{row.kind}</span> <strong>{outcome(row)}</strong><p>{row.run_id||row.operation_id}</p>{row.resource_wait&&<p>Waiting for {row.resource_wait.resource} — held by run {row.resource_wait.holder_run_id}</p>}{(row.error||row.result?.error)&&<p className="error-text">{row.error||row.result.error}</p>}</div><div className="button-row">{runs.some(run=>run.run_id===row.run_id)&&<button onClick={()=>openRun(row.run_id)}>View run</button>}{(row.observed_state==='requires_reconciliation'||row.state==='requires_recovery'||row.state==='accepted')&&<button onClick={()=>command(async()=>{const confirmation=prompt(`Only reconcile a stopped runner with no live descendants. Enter: RECONCILE ${row.operation_id}`);if(confirmation){await api(`/operations/${row.operation_id}/reconcile`,{confirmation});setNotice('Operation resolved; no command was replayed.');}})}>Review interrupted operation</button>}</div></section>)}
        <section className="card"><div className="section-heading"><div><h2>Survey runs</h2><p>Persisted status, with individual attempts available in each run.</p></div><button className="primary" onClick={newRun}>New run</button></div>{availability==='database_missing'&&<p className="empty">No existing pipeline database found. This dashboard will not create it.</p>}<div className="table-scroll"><table><thead><tr><th>Survey</th><th>Status</th><th>Latest stage</th><th>Started</th><th/></tr></thead><tbody>{runs.map(run=><tr key={run.run_id}><td><strong>{display(run.survey_id)}</strong><small>{run.run_id}</small></td><td><span className={'badge '+run.status}>{run.status}</span>{legacyDisposition(run.run_id,execution)&&<small>{legacyDisposition(run.run_id,execution)}</small>}</td><td>{display(run.latest_stage?.stage_name)}{run.latest_stage?.error_message&&<small className="error-text">{run.latest_stage.error_message}</small>}</td><td>{run.started_at?new Date(run.started_at).toLocaleString():'—'}</td><td><button onClick={()=>openRun(run.run_id)}>Open</button></td></tr>)}</tbody></table></div><div className="button-row">{before&&<button onClick={()=>setBefore(null)}>First page</button>}{next&&<button onClick={()=>setBefore(next)}>Older runs</button>}</div>{!runs.length&&availability!=='database_missing'&&<p className="empty">No runs recorded yet.</p>}</section></>}
      {page==='new'&&<RunForm {...{inputs,preflight,locked,update,check,start}}/>}
      {page==='detail'&&detail&&<RunDetail {...{detail,logs,locked,sending,control,decide}} resume={(recovery=false)=>{setInputs(resumeInputs(detail,recovery));setPreflight(null);setPage('new');setNotice('');}} copy={path=>command(async()=>{await navigator.clipboard.writeText(path);setNotice('Path copied.');})}/>}
      {page==='detail'&&!detail&&<p className="empty">Loading run…</p>}<footer>Pipeline Console V1 · Local controls never imply cancellation of remote WebODM tasks.</footer>
    </main></div>;
}

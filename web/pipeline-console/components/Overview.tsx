import {Value} from '../lib/api';
import {busy,operationAttention,outcome,overviewOperations,stageLabel,statusLabel,historyStatus} from '../lib/contract.mjs';
import {Empty,Panel,StatusBadge} from './Ui';

export default function Overview({runs,operations,execution,availability,before,next,openRun,newRun,reconcile,firstPage,older}: {
  runs:Value[];operations:Value[];execution:Value;availability:string;before:string|null;next:string|null;
  openRun:(run:string)=>void;newRun:()=>void;reconcile:(row:Value)=>void;firstPage:()=>void;older:()=>void;
}) {
  const latest=overviewOperations(operations);
  const attention=latest.filter(operationAttention);
  const active=latest.filter(row=>busy(row)&&!operationAttention(row));
  const card=(row:Value)=>{
    const run=runs.find(run=>run.run_id===row.run_id);
    const interrupted=row.observed_state==='requires_reconciliation'||row.state==='requires_recovery';
    return <article className="operation" key={row.operation_id}><div className="operation-main"><div className="operation-title"><strong>{run?.survey_id|| (row.kind==='preflight'?'Input and storage check':'Survey processing')}</strong><StatusBadge status={interrupted?'requires_reconciliation':row.state} label={row.result?.pipeline||interrupted?outcome(row):statusLabel(row.state)}/></div><p>{interrupted?'Review interrupted local ownership before starting more work.':row.state==='awaiting_review'?'Open this run to inspect and approve the processing result.':row.resource_wait?'Waiting for '+row.resource_wait.resource+'.':'Current stage: '+stageLabel(run?.latest_stage?.stage_name)}</p><details><summary>Technical operation receipt</summary><dl className="facts identifiers"><div><dt>Run ID</dt><dd><code>{row.run_id||'No run recorded'}</code></dd></div><div><dt>Operation ID</dt><dd><code>{row.operation_id}</code></dd></div></dl>{(row.error||row.result?.error)&&<pre className="log">{row.error||row.result.error}</pre>}{interrupted&&<><p>Local reconciliation requires a stopped runner and no live descendants. It does not restart WebODM or resume the pipeline.</p><button onClick={()=>reconcile(row)}>Review interrupted operation</button></>}</details></div>{run&&<button onClick={()=>openRun(row.run_id)}>Open run</button>}</article>;
  };
  return <>
    <section className="metrics" aria-label="Workstation summary"><article><small>Local execution</small><strong>{execution.occupied} of {execution.max_runs} slots used</strong><p>{execution.blocked?'Local review required before starting.':execution.available+' slot(s) available. No queue.'}</p></article><article><small>Needs attention</small><strong>{attention.length} local item{attention.length===1?'':'s'}</strong><p>Review interrupted work or pending decisions.</p></article><article><small>WebODM status</small><strong>Last observed</strong><p>Remote tasks may continue while the pipeline is paused.</p></article></section>
    <div className="overview-groups"><Panel title="Needs attention" description="Resolve local ownership issues and pending decisions here.">{attention.length?attention.map(card):<Empty title="No local operation needs review">Survey failures remain visible in the history below.</Empty>}</Panel>
    <Panel title="Active processing" description="Local invocations and input checks currently in progress.">{active.length?active.map(card):<Empty title="No active local processing">Start a new run, or open a previous survey to prepare a resume.</Empty>}</Panel></div>
    <Panel title="Survey history" description="Recorded survey outcomes. Open a run for progress, outputs and technical details." action={<button className="primary" onClick={newRun}>New run</button>}>
      {availability==='database_missing'?<Empty title="No survey history yet">The overview does not create a missing pipeline database.</Empty>:!runs.length?<Empty title="No runs recorded yet"/>:<div className="table-scroll"><table><thead><tr><th>Survey</th><th>Status</th><th>Latest stage</th><th>Started</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{runs.map(run=><tr key={run.run_id}><td><strong>{run.survey_id||'Survey ID not recorded'}</strong></td><td><StatusBadge status={historyStatus(run)}/></td><td>{stageLabel(run.latest_stage?.stage_name)}</td><td className="nowrap">{run.started_at?new Date(run.started_at).toLocaleString():'Not recorded'}</td><td><button onClick={()=>openRun(run.run_id)} aria-label={'Open survey '+(run.survey_id||run.run_id)}>Open run</button></td></tr>)}</tbody></table></div>}
      {(before||next)&&<div className="button-row pagination">{before&&<button onClick={firstPage}>Latest runs</button>}{next&&<button onClick={older}>Older runs</button>}</div>}
    </Panel>
  </>;
}

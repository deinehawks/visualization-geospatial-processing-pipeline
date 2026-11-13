import {useState} from 'react';
import {Value,display} from '../lib/api';
import {busy,duration,latestStages,runPresentation,savedTask4Profile,stageLabel,statusLabel} from '../lib/contract.mjs';
import {Empty,Panel,StatusBadge,Tabs} from './Ui';

export default function RunDetail({detail,logs,lockReason,maintenanceReason,sending,resume,control,decide,copy,reconcile}: {
  detail:Value; logs:Value[]; lockReason:string; maintenanceReason:string; sending:boolean; resume:(recovery?:boolean)=>void;
  control:(action:string)=>void; decide:(action:string)=>void; copy:(path:string)=>void; reconcile:(operation:Value)=>void;
}) {
  const [tab,setTab]=useState('progress');
  const view=runPresentation(detail);
  const current=view.operation;
  const review=view.review ? current?.review : null;
  const profile=savedTask4Profile(detail);
  const resumeReason=lockReason || (view.localReview ? 'Review interrupted local ownership first.' : current?.controls_available || (current && busy(current)) ? 'This local invocation is still active.' : view.status==='completed' ? 'This run is completed.' : '');
  const stages=latestStages(detail.stages);
  const progress=stages.filter(row=>row.stage_name!=='webodm'||!stages.some(other=>['webodm_task4','webodm_task2'].includes(other.stage_name)&&other.id>row.id));
  return <>
    <section className="card run-summary">
      <div className="section-heading"><div><p className="eyebrow">SURVEY RUN</p><h2 className="survey-title">{display(detail.run.survey_id)}</h2><div className="summary-status"><StatusBadge status={view.status}/><span>Current stage: <strong>{stageLabel(view.latest?.stage_name)}</strong></span></div></div>
        <div className="button-row"><button className="primary" disabled={!!resumeReason} aria-describedby="resume-reason" onClick={()=>resume()}>Prepare resume</button><button disabled={sending||!current?.controls_available} onClick={()=>control('pause')}>Pause locally</button><span className="action-divider"/><button className="danger-outline" disabled={sending||!current?.controls_available} onClick={()=>control('stop')}>Stop locally</button></div></div>
      <div className={'guidance '+(view.localReview?'warning':'')}><strong>Next safe action</strong><p>{view.next}</p></div>
      {resumeReason && <p className="muted small" id="resume-reason">Resume unavailable: {resumeReason}</p>}
      {!current?.controls_available && <p className="small muted">Pause and Stop are unavailable because no controllable local runner is active.</p>}
      {detail.console_operations[0]?.control_commands?.map((command:Value)=><p className="small" key={command.command_id}>Local {command.action}: <strong>{statusLabel(command.state)}</strong></p>)}
      <div className="summary-footer"><span className="path"><strong>Dataset</strong> {display(detail.run.source_dir)}</span>{detail.webodm_project_links?.map((link:Value)=><a className="button" key={link.project_id} href={link.url} target="_blank" rel="noreferrer">Open WebODM project {link.project_id} ↗</a>)}</div>
    </section>
    {review && <Panel title="Review the processing result" description="Open WebODM and inspect the result before continuing. Closing this page does not approve the result." className="review">
      {review.webodm_url && !review.webodm_url.startsWith('[') && <a className="button" target="_blank" rel="noreferrer" href={review.webodm_url.replace(/\/$/,'')+'/dashboard/'+review.project_id}>Open project {review.project_id} ↗</a>}
      <div className="button-row"><button className="primary" disabled={sending} onClick={()=>decide('approve')}>Approve and continue</button><button disabled={sending} onClick={()=>decide('fail')}>Fail review</button></div>
    </Panel>}
    <Tabs value={tab} onChange={setTab}>
      {tab==='progress' && <div className="detail-grid"><Panel title="Pipeline progress" description="Latest attempt for each stage. Earlier attempts remain in Technical details.">
        {!progress.length ? <Empty title="No stage recorded yet">The latest stage will appear here when available.</Empty> : <ol className="stage-list">{progress.map((stage:Value)=><li key={stage.id}><span className={'stage-marker '+stage.status}/><div><strong>{stageLabel(stage.stage_name)}</strong><small>{duration(stage.runtime_seconds)}{['failed','requires_recovery'].includes(stage.status)?' · See Technical details for the recorded error.':''}</small></div><StatusBadge status={stage.status}/></li>)}</ol>}
      </Panel><div><Panel title="WebODM processing" description="Last observed status from the pipeline. This page does not perform a live WebODM check.">
        {!detail.operations.length ? <Empty title="No remote task recorded"/> : detail.operations.map((task:Value)=><article className="remote-task" key={task.id}><div className="section-heading"><strong>{task.operation_key==='task4'?'Orthomosaic (Task 4)':task.operation_key==='task2'?'3D model (Task 2)':task.operation_key}</strong><StatusBadge status={task.remote_status}/></div><p className="small">{task.task_id ? statusLabel(task.local_status) : 'No task UUID recorded for this historical binding.'}</p>{task.last_observed_at && <p className="small">Observed: {new Date(task.last_observed_at).toLocaleString()}</p>}{['failed','canceled'].includes(task.remote_status)&&<p className="reason">Inspect the exact project and task in Technical details before remote recovery.</p>}</article>)}
      </Panel>{profile && <Panel title="Saved large survey profile"><dl className="facts"><div><dt>Images per model</dt><dd>{profile.split}</dd></div><div><dt>Overlap</dt><dd>{profile.overlap} m</dd></div></dl><p className="small">These settings belong to the saved task and are read only.</p></Panel>}</div></div>}
      {tab==='outputs' && <Panel title="Recorded outputs" description="Copy a path to open it on your workstation. These are recorded paths; current file availability has not been checked.">
        {!detail.artifacts.length ? <Empty title="No outputs recorded yet">Completed stages will add their output paths here.</Empty> : detail.artifacts.map((artifact:Value,index:number)=><div className="artifact" key={index}><strong>{artifact.logical_name.replaceAll('_',' ')}</strong><code>{artifact.path}</code><button onClick={()=>copy(artifact.path)}>Copy path</button></div>)}
      </Panel>}
      {tab==='technical' && <>
        <Panel title="Identifiers and recovery" description="Run ID identifies the survey run. Operation ID identifies one local invocation or check.">
          <dl className="facts identifiers"><div><dt>Run ID</dt><dd><code>{detail.run.run_id}</code></dd></div>{current && <div><dt>Latest operation ID</dt><dd><code>{current.operation_id}</code></dd></div>}</dl>
          {view.localReview && current && <div className="guidance warning"><strong>Interrupted local operation</strong><p>Reconcile only after its runner and child processes have stopped. Reconciliation clears local ownership; it does not restart WebODM or resume processing.</p><button disabled={sending} onClick={()=>reconcile(current)}>Review interrupted operation</button></div>}
          <div className="remote-identities">{detail.operations.map((task:Value)=><div key={task.id}><strong>{task.operation_key}</strong><p>Project ID: {display(task.project_id)}</p><code>Task ID: {display(task.task_id)}</code><p>Last observed WebODM status: {statusLabel(task.remote_status)}</p></div>)}</div>
          <p>For a remote restart, inspect this exact existing task in WebODM. Request help if the task identity or outcome is uncertain.</p>
          {review && <div className="remote-identities">{Object.entries(review.tasks || {}).filter(([,value])=>(value as Value)?.id).map(([name,value])=><div key={name}>{name}: project {review.project_id}<code>{(value as Value).id}</code></div>)}</div>}
          <details><summary>Expert tile recovery</summary><p>Use for damaged staging tiles. Existing tiles move to quarantine and the original run identity is retained.</p><button disabled={!!(resumeReason||maintenanceReason)} onClick={()=>resume(true)}>Prepare tile recovery</button>{(resumeReason||maintenanceReason)&&<p className="small">{resumeReason||maintenanceReason}</p>}</details>
          <details><summary>Local operation history</summary>{detail.console_operations.map((row:Value)=><div className="receipt" key={row.operation_id}><StatusBadge status={row.state}/><p>{row.kind} · Operation ID: {row.operation_id}</p></div>)}</details>
        </Panel>
        <Panel title="Complete stage history" description="Earlier attempts remain visible. The latest attempt determines the current stage outcome."><div className="table-scroll"><table><thead><tr><th>Stage</th><th>Attempt</th><th>Status</th><th>Duration</th><th>Recorded details</th></tr></thead><tbody>{detail.stages.map((stage:Value)=><tr key={stage.id}><td><strong>{stageLabel(stage.stage_name)}</strong><small>{stage.stage_name}</small></td><td>{stage.attempt_number}{stage.authoritative_attempt&&<small>Latest</small>}</td><td><StatusBadge status={stage.status}/></td><td className="nowrap">{duration(stage.runtime_seconds)}</td><td className="error-detail">{display(stage.error_message)}</td></tr>)}</tbody></table></div>{!detail.stages.length&&<Empty title="No stage history yet"/>}</Panel>
        <Panel title="Logs" description="Recorded log tail for this run. Progress is shown as reported."><pre className="log">{detail.console_operations.find((row:Value)=>row.kind==='run'&&row.log_tail)?.log_tail || (logs.length ? logs.map(row=>row.message).join('\n'):'No attributed log lines available yet.')}</pre></Panel>
      </>}
    </Tabs>
  </>;
}

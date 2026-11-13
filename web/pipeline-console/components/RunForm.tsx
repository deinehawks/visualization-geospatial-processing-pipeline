import {useState} from 'react';
import {Value} from '../lib/api';
import {checkDisabledReason,needsResumeReview,passing,startDisabledReason,tileConfirmation,statusLabel} from '../lib/contract.mjs';
import {Empty,StatusBadge} from './Ui';

export default function RunForm({inputs,preflight,lockReason,resumeKnownOptions,savedProfile,boundTask4,update,check,start}: {
  inputs:Value;preflight:Value|null;lockReason:string;resumeKnownOptions:string[];savedProfile:Value|null;boundTask4:boolean;
  update:(field:string,value:any)=>void;check:()=>void;start:()=>void;
}) {
  const [reviewed,setReviewed]=useState(false);
  const historical=inputs.action!=='new';
  const unknown=needsResumeReview(inputs,resumeKnownOptions);
  const checkReason=checkDisabledReason(inputs,lockReason,reviewed,resumeKnownOptions);
  const startReason=startDisabledReason(inputs,preflight,lockReason,reviewed,resumeKnownOptions);
  const report=preflight?.result?.preflight || preflight?.preflight;
  const split=report?.task4_split;
  const large=inputs.task4_split!=null;
  const readOnlyProfile=historical&&(boundTask4||!!savedProfile);
  const step=passing(preflight)?3:preflight?1:0;
  const known=(key:string)=>!historical||resumeKnownOptions.includes(key);
  const fieldNote=(key:string)=>known(key)?null:<small className="unknown-setting">Not recorded — review this choice</small>;
  return <>
    <ol className="steps" aria-label="Run preparation steps">{['Choose inputs','Check','Review',historical?'Resume':'Start'].map((label,index)=><li key={label} className={index<=step?'reached':''} aria-current={index===step?'step':undefined}><span>{index+1}</span>{label}</li>)}</ol>
    <div className="form-grid"><section className="card">
      <div className="section-heading"><div><h2>{historical?'Original survey inputs':'Survey inputs'}</h2><p>{historical?'The original run, source and output identity are retained.':'Choose the dataset and the outputs you need.'}</p></div><span className="section-number">01</span></div>
      <div className="field-group">
        <label>Dataset name or absolute folder path<input value={inputs.survey} disabled={historical} onChange={e=>update('survey',e.target.value)} placeholder="Enter the dataset name or folder path"/></label>
        <div className="two-columns"><label>Survey ID <small>(blank = automatic)</small><input value={inputs.survey_id || ''} disabled={historical} onChange={e=>update('survey_id',e.target.value || null)} placeholder="AH-026136"/></label><label>Year<input type="number" min={2000} max={2100} value={inputs.year} disabled={historical} onChange={e=>update('year',Number(e.target.value))}/></label></div>
        <label>What should the pipeline make?<select value={inputs.mode || ''} onChange={e=>update('mode',e.target.value)}><option value="" disabled>Original mode not recorded — choose explicitly</option><option value="task4">Orthomosaic (Task 4)</option><option value="task2">3D model (Task 2)</option><option value="both">Orthomosaic and 3D model</option></select></label>
      </div>
      {inputs.mode==='task4'&&inputs.action!=='rebuild_tiles'&&<section className="form-section"><h3>Large survey settings</h3>
        {readOnlyProfile?<div className="guidance"><strong>Saved Task 4 settings · read only</strong><p>{savedProfile?savedProfile.split+' images per model · '+savedProfile.overlap+' m overlap.':'No split profile was recorded for this existing task.'} Resume continues the saved task with its existing options.</p></div>:<>
          <label className="check"><input type="checkbox" checked={large} onChange={e=>update('task4_split',e.target.checked?400:null)}/><span>Split into smaller models<small>For large datasets processed on one WebODM node.</small></span></label>
          {large&&<div className="inset"><p className="small">Default: 400 images per model with 100 m overlap. Select a node with enough memory and scratch storage.</p><div className="two-columns"><label>Images per model<input type="number" min={1} value={inputs.task4_split} onChange={e=>update('task4_split',e.target.value?Number(e.target.value):null)}/></label><label>Overlap (metres)<input type="number" min={1} value={inputs.task4_split_overlap ?? 100} onChange={e=>update('task4_split_overlap',e.target.value?Number(e.target.value):null)}/></label></div><label>WebODM node ID <small>(required)</small><input type="number" min={1} value={inputs.node_id ?? ''} onChange={e=>update('node_id',e.target.value?Number(e.target.value):null)}/></label></div>}
        </>}
      </section>}
      <details className="form-section" open={unknown?true:undefined}><summary>Advanced run settings{unknown?' · Review required':''}</summary><p className="small">These choices affect image selection and the processing node.</p>
        <div className="two-columns"><label>Date hint<input placeholder="YYYYMMDD" value={inputs.date || ''} onChange={e=>update('date',e.target.value || null)}/></label><label>UAV folder{fieldNote('uav')}<input value={inputs.uav || ''} onChange={e=>update('uav',e.target.value || null)} placeholder="Blank = no UAV folder filter"/></label></div>
        {!large&&<label>WebODM node ID{fieldNote('node_id')}<input type="number" min={1} value={inputs.node_id || ''} onChange={e=>update('node_id',e.target.value?Number(e.target.value):null)} placeholder="Blank = configured node choice"/></label>}
        {[['rgb','Use DJI RGB *_D.JPG images only'],['disable_cross_run','Disable cross-run filtering'],['keep_workspace','Keep workspace after full success']].map(([field,label])=><div key={field}><label className="check"><input type="checkbox" checked={!!inputs[field]} onChange={e=>update(field,e.target.checked)}/><span>{label}</span></label>{fieldNote(field)}</div>)}
        {unknown&&<div className="guidance warning"><strong>Review older terminal-run settings</strong><p>Some choices were never recorded. Empty or unchecked fields are choices you make now, not recovered values.</p><label className="check"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/><span>I reviewed RGB, cross-run, UAV folder and node settings</span></label></div>}
      </details>
      {inputs.action==='rebuild_tiles'&&<section className="form-section"><h3>Expert tile recovery</h3><p>Existing staging tiles move to quarantine. Keep that copy until the new outputs are checked.</p><label>Affected task<select value={inputs.rebuild_task || ''} onChange={e=>update('rebuild_task',e.target.value)}><option value="" disabled>Select task</option><option value="task2">Task 2</option><option value="task4">Task 4</option></select></label><label>Type the exact confirmation<code>{tileConfirmation(inputs)}</code><input value={inputs.confirmation} onChange={e=>update('confirmation',e.target.value)}/></label></section>}
      <div className="form-actions"><button disabled={!!checkReason} onClick={check} aria-describedby="check-reason">Check inputs &amp; storage</button><button className="primary" disabled={!!startReason} onClick={start} aria-describedby="start-reason">{inputs.action==='new'?'Start processing':inputs.action==='resume'?'Resume run':'Quarantine and rebuild'}</button></div>
      {checkReason&&<p id="check-reason" className="reason" role="status">{checkReason}</p>}{startReason&&<p id="start-reason" className="small muted" role="status">{startReason}</p>}
      <p className="small muted">Changing any input requires a new check. Starting runs the pipeline and writes its normal outputs.</p>
    </section>
    <aside className="card preflight"><div className="section-heading"><div><h2>Review before starting</h2><p>Check the source and storage result.</p></div><span className="section-number">02</span></div>
      <dl className="facts"><div><dt>Outputs</dt><dd>{inputs.mode==='task4'?'Orthomosaic':inputs.mode==='task2'?'3D model':inputs.mode==='both'?'Orthomosaic and 3D':'Choose a mode'}</dd></div><div><dt>Survey</dt><dd>{inputs.survey_id||'Assigned automatically'}</dd></div>{(large||savedProfile)&&<div><dt>Split profile</dt><dd>{savedProfile?.split||inputs.task4_split} images / {savedProfile?.overlap||inputs.task4_split_overlap||100} m</dd></div>}</dl>
      {preflight&&<div className="check-result"><StatusBadge status={passing(preflight)?'completed':preflight.state} label={passing(preflight)?'Inputs and storage ready':statusLabel(preflight.state)}/>{(preflight.error||preflight.result?.error)&&<p className="reason">The check could not complete. Review the details below and check again.</p>}{preflight.result?.candidates?.map((path:string)=><button className="candidate" key={path} onClick={()=>update('survey',path)}>{path}</button>)}</div>}
      {report&&<><div className={'guidance '+(report.report.ok?'success':'warning')}><strong>{report.report.ok?'Check passed':'Storage check failed'}</strong><p>{report.report.ok?'Review the selected inputs, then start when ready.':'Review the failed storage volume, free space safely, then check again.'}</p></div><dl className="facts"><div><dt>Images selected</dt><dd>{report.report.source_image_count ?? 'Not recorded'}</dd></div><div><dt>Resolved source</dt><dd className="path">{report.source_dir}</dd></div></dl>
        {split&&<div className="inset"><strong>Validated large survey profile</strong><p className="small">Node {split.processing_node} · {split.split} images per model · {split.split_overlap_meters} m overlap · about {split.approximate_base_submodels} base models.</p></div>}
        <details open={!report.report.ok}><summary>Storage volumes</summary>{report.report.volumes?.map((volume:Value)=><div className="volume" key={volume.volume}><div className="section-heading"><strong className="path">{volume.volume}</strong><StatusBadge status={volume.ok?'completed':'failed'} label={volume.ok?'Enough space':'More space needed'}/></div><p className="small">{volume.roles?.join(' · ')}</p><dl className="facts compact">{[['Free',volume.free_bytes],['Estimated writes',volume.required_bytes],['Reserve',volume.reserve_bytes]].map(([name,value])=><div key={String(name)}><dt>{name}</dt><dd>{(Number(value)/1073741824).toFixed(2)} GiB</dd></div>)}</dl></div>)}</details>
      </>}
      {!preflight&&<Empty title="Ready to check">Choose your inputs, then select Check inputs &amp; storage.</Empty>}
      {(inputs.run_id||preflight?.log_tail||preflight?.error||preflight?.result?.error)&&<details><summary>Technical check details</summary>{inputs.run_id&&<><p>Original run ID</p><code>{inputs.run_id}</code></>}{(preflight?.error||preflight?.result?.error)&&<pre className="log">{preflight.error||preflight.result.error}</pre>}{preflight?.log_tail&&<pre className="log">{preflight.log_tail}</pre>}</details>}
    </aside></div>
  </>;
}

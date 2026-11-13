export const newInputs = () => ({action:'new', survey:'', survey_id:null, year:2026, mode:'task4',
  run_id:null, date:null, uav:null, node_id:null, disable_cross_run:false, rgb:false,
  keep_workspace:false, task4_split:null, task4_split_overlap:null, rebuild_task:null, confirmation:''});

export function resumeInputs(detail, recovery = false) {
  return {...newInputs(), ...(detail.resume_options || {}), action:recovery ? 'rebuild_tiles' : 'resume',
    survey:detail.run.source_dir || '', survey_id:detail.run.survey_id,
    run_id:detail.run.run_id, year:detail.run.year, mode:detail.mode || null};
}
export function passing(operation) {
  return operation?.kind === 'preflight' && operation.state === 'finished' &&
    operation.result?.preflight?.report?.ok === true;
}
export function busy(operation) {
  return !!operation && (operation.resource_busy === true || !['finished','failed','resolved','canceled'].includes(operation.state));
}
export function canDispatch(execution, legacy = [], maintenance = false) {
  return !!execution && !execution.blocked && execution.available > 0 && legacy.length === 0 &&
    (!maintenance || execution.occupied === 0);
}
export function legacyDisposition(runId, execution) {
  return execution?.legacy_reconciled?.some(row=>row.run_id===runId)
    ? 'Locally reconciled; WebODM unverified' : '';
}
export function tileConfirmation(inputs) {
  return 'REBUILD QGIS TILES ' + (inputs.rebuild_task || 'task2') + ' FOR RUN ' + inputs.run_id;
}
export function outcome(operation) {
  const result = operation?.result?.pipeline;
  if (operation?.observed_state === 'requires_reconciliation' || operation?.state === 'requires_recovery') return 'Needs reconciliation';
  if (result?.paused) return 'Paused locally — WebODM may continue';
  if (result?.aborted) return 'Stopped locally';
  if (result?.success === true) return 'Completed';
  if (result?.success === false) return result.partially_completed ? 'Partially completed' : 'Failed';
  return operation?.state?.replaceAll('_',' ') || 'Unknown';
}
export function changedInput(inputs, field, value) {
  const next = {...inputs, [field]:value};
  if ((field === 'mode' && value !== 'task4') || (field === 'task4_split' && value === null)) {
    next.task4_split = null;
    next.task4_split_overlap = null;
  }
  return {inputs:next, preflight:null};
}
export function savedTask4Profile(detail) {
  const task = detail?.operations?.find(row=>row.operation_key === 'task4');
  const split = Number(task?.options?.split);
  if (!Number.isInteger(split) || split <= 0) return null;
  const overlap = Number(task.options['split-overlap'] ?? 100);
  if (!Number.isInteger(overlap) || overlap <= 0) return null;
  return {split, overlap, project_id:task.project_id, task_id:task.task_id};
}
export function needsResumeReview(inputs, knownOptions = []) {
  return inputs.action !== 'new' && ['rgb','disable_cross_run','uav','node_id']
    .some(key=>!knownOptions.includes(key));
}
export function checkDisabledReason(inputs, lockedReason = '', reviewed = false, knownOptions = []) {
  if (lockedReason) return lockedReason;
  if (!inputs.survey) return 'Enter or select the survey dataset.';
  if (!inputs.mode) return 'Choose the processing mode for this run.';
  if (inputs.task4_split != null) {
    if (inputs.mode !== 'task4' || inputs.action === 'rebuild_tiles') return 'Large survey splitting is for Task 4-only runs.';
    if (!Number.isInteger(inputs.task4_split) || inputs.task4_split <= 0) return 'Enter a positive whole number of images per model.';
    if (!inputs.node_id) return 'Choose a WebODM processing node for the large survey.';
  }
  if (inputs.task4_split_overlap != null && (!Number.isInteger(inputs.task4_split_overlap) || inputs.task4_split_overlap <= 0)) return 'Enter a positive whole number of metres for overlap.';
  if (inputs.task4_split_overlap != null && inputs.task4_split == null) return 'Choose a split size before setting overlap.';
  if (needsResumeReview(inputs, knownOptions) && !reviewed) return 'Review and confirm the historical run settings below.';
  if (inputs.action === 'rebuild_tiles' && (!inputs.rebuild_task || inputs.confirmation !== tileConfirmation(inputs))) return 'Select the tile task and enter the exact recovery confirmation.';
  return '';
}
export function startDisabledReason(inputs, preflight, lockedReason = '', reviewed = false, knownOptions = []) {
  const reason = checkDisabledReason(inputs, lockedReason, reviewed, knownOptions);
  if (reason) return reason;
  if (!preflight) return 'Check inputs and storage first. Each input change requires a new check.';
  if (!passing(preflight)) return busy(preflight) ? 'Checking inputs and storage. Wait for the result.' : 'Resolve the failed check, then check inputs and storage again.';
  return '';
}
const labels = {
  running:'Processing', completed:'Completed', failed:'Failed', paused:'Paused locally',
  requires_recovery:'Needs recovery', requires_reconciliation:'Needs local review',
  partially_completed:'Partly completed', accepted:'Preparing', preparing:'Preparing',
  awaiting_review:'Waiting for your review', resolved:'Locally resolved', canceled:'Canceled',
  finished:'Finished locally', stopped:'Stopped locally', reattached:'Connected to saved task', legacy_import:'Historical record',
  pending:'Not started', skipped:'Skipped', downloaded:'Downloaded', ready:'Ready'
};
export function statusLabel(value) { return labels[value] || (value ? value.replaceAll('_',' ') : 'Not recorded'); }
export function statusTone(value) {
  if (['completed','ready','downloaded'].includes(value)) return 'success';
  if (['failed','requires_recovery','requires_reconciliation'].includes(value)) return 'error';
  if (['paused','partially_completed','awaiting_review'].includes(value)) return 'warning';
  if (['running','preparing','accepted','reattached'].includes(value)) return 'info';
  return 'neutral';
}
const stages = {data_segregation:'Prepare images',cross_run_filter:'Check flight paths',kml_boundary:'Prepare boundary',
  webodm:'WebODM processing',webodm_task4:'Make orthomosaic (Task 4)',webodm_task2:'Make 3D model (Task 2)',
  quality_gate:'Review processing results',qgis:'Clip maps and create tiles'};
export function stageLabel(value) { return stages[value] || (value ? value.replaceAll('_',' ') : 'Not recorded'); }
export function duration(value) {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) < 0) return 'Not recorded';
  const seconds=Math.round(Number(value));
  const hours=Math.floor(seconds/3600), minutes=Math.floor(seconds%3600/60), remainder=seconds%60;
  return [hours ? hours+'h' : '',minutes ? minutes+'m' : '',remainder || (!hours && !minutes) ? remainder+'s' : ''].filter(Boolean).join(' ');
}
export function latestStages(stages = []) {
  const latest=new Map();
  for (const stage of [...stages].sort((a,b)=>(a.id || 0)-(b.id || 0))) latest.set(stage.stage_name,stage);
  return [...latest.values()].sort((a,b)=>(a.id || 0)-(b.id || 0));
}
export function currentRunOperation(operations = []) {
  return operations.filter(row=>row.kind !== 'preflight')
    .sort((a,b)=>(b.created_at || '').localeCompare(a.created_at || ''))[0] || null;
}
export function runPresentation(detail) {
  const operation=currentRunOperation(detail.console_operations || []);
  const latest=latestStages(detail.stages || []).at(-1);
  const localReview=operation && (operation.observed_state==='requires_reconciliation' || operation.state==='requires_recovery');
  const review=operation?.state==='awaiting_review';
  const stageStatus=latest?.status;
  const result=operation?.result?.pipeline;
  const operationStatus=result?.paused ? 'paused' : result?.aborted ? 'stopped' :
    result?.success===false || operation?.state==='failed' ? 'failed' : null;
  const status=localReview ? 'requires_reconciliation' : review ? 'awaiting_review' :
    operationStatus || (['failed','requires_recovery','paused','running'].includes(stageStatus) ? stageStatus : detail.run.status);
  const failedTasks=(detail.operations || []).filter(row=>['failed','canceled'].includes(row.remote_status));
  let next='Check the saved inputs and storage before resuming.';
  if (localReview) next='Review the interrupted local operation in Technical details before resuming.';
  else if (review) next='Open the WebODM result, inspect it, then approve or fail the review.';
  else if (status==='completed') next='Open Outputs and check the published results.';
  else if (status==='running') next='Processing is underway. Follow the current stage below.';
  else if (failedTasks.length) next='Inspect the recorded task in WebODM. Its last observed status needs attention; local resume does not replace it.';
  else if (status==='requires_recovery') next='Review the latest failure in Technical details before choosing a recovery action.';
  return {status,next,latest,operation,localReview,review};
}
export const runTabs = ['progress','outputs','technical'];
export function tabForKey(current,key) {
  const index=runTabs.indexOf(current);
  if (key==='Home') return runTabs[0];
  if (key==='End') return runTabs.at(-1);
  if (key==='ArrowRight') return runTabs[(index+1)%runTabs.length];
  if (key==='ArrowLeft') return runTabs[(index+runTabs.length-1)%runTabs.length];
  return null;
}
export function operationAttention(row) {
  return row.observed_state==='requires_reconciliation' || row.state==='requires_recovery' ||
    (row.kind!=='preflight' && (row.state==='failed' || row.state==='awaiting_review' || row.result?.pipeline?.success===false));
}
export function historyStatus(run) {
  return ['failed','requires_recovery','paused','running'].includes(run.latest_stage?.status)
    ? run.latest_stage.status : run.status;
}
export function overviewOperations(operations) {
  const latest=new Map();
  for (const row of [...operations].sort((a,b)=>(a.created_at || '').localeCompare(b.created_at || ''))) {
    latest.set((row.run_id || row.operation_id)+':'+(row.kind==='preflight'?'check':'run'),row);
  }
  return [...latest.values()].sort((a,b)=>(b.created_at || '').localeCompare(a.created_at || ''));
}

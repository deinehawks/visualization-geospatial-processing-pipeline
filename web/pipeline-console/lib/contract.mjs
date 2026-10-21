export const newInputs = () => ({action:'new', survey:'', survey_id:null, year:2026, mode:'task4',
  run_id:null, date:null, uav:null, node_id:null, disable_cross_run:false, rgb:false,
  keep_workspace:false, rebuild_task:null, confirmation:''});

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
  return operation && (operation.resource_busy === true || !['finished','failed','resolved','canceled'].includes(operation.state));
}

export function tileConfirmation(inputs) {
  return `REBUILD QGIS TILES ${inputs.rebuild_task || 'task2'} FOR RUN ${inputs.run_id}`;
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
  return {inputs:{...inputs, [field]:value}, preflight:null};
}

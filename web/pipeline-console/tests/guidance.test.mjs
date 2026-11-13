import test from 'node:test';
import assert from 'node:assert/strict';
import {newInputs,changedInput,checkDisabledReason,startDisabledReason,savedTask4Profile,resumeInputs,
  runPresentation,latestStages,duration,statusLabel,statusTone,stageLabel,tabForKey,overviewOperations,operationAttention,historyStatus} from '../lib/contract.mjs';

const pass={kind:'preflight',state:'finished',result:{preflight:{report:{ok:true}}}};
const known=['rgb','disable_cross_run','uav','node_id'];
const detail=()=>({run:{run_id:'fixture-run',status:'running'},stages:[],console_operations:[],operations:[]});
test('new run requires inputs and a passing check; edits invalidate it',()=>{
  const inputs={...newInputs(),survey:'fixture-survey'};
  assert.match(checkDisabledReason(newInputs()),/dataset/);
  assert.match(startDisabledReason(inputs,null),/Check inputs/);
  assert.equal(startDisabledReason(inputs,pass),'');
  assert.equal(changedInput(inputs,'survey','changed').preflight,null);
  assert.match(startDisabledReason(inputs,pass,'All slots occupied'),/occupied/);
  assert.match(startDisabledReason(inputs,{kind:'preflight',state:'running'}),/Wait/);
  assert.match(startDisabledReason(inputs,{kind:'preflight',state:'finished',result:{preflight:{report:{ok:false}}}}),/failed/);
});
test('large survey needs node and positive numbers; changing mode removes split overrides',()=>{
  const inputs={...newInputs(),survey:'fixture',task4_split:400};
  assert.match(checkDisabledReason(inputs),/node/);
  assert.equal(checkDisabledReason({...inputs,node_id:3}),'');
  assert.match(checkDisabledReason({...inputs,node_id:3,task4_split:1.5}),/whole number/);
  assert.match(checkDisabledReason({...inputs,node_id:3,task4_split_overlap:0}),/metres/);
  assert.equal(changedInput(inputs,'mode','both').inputs.task4_split,null);
  assert.equal(changedInput({...inputs,task4_split_overlap:100},'task4_split',null).inputs.task4_split_overlap,null);
});
test('historical choices need explicit review and saved profiles do not become CLI overrides',()=>{
  const d={...detail(),mode:'task4',run:{run_id:'fixture-run',source_dir:'fixture'},operations:[{operation_key:'task4',task_id:'exact-task',project_id:12,options:{split:400,'split-overlap':100}}]};
  const inputs=resumeInputs(d);
  assert.match(checkDisabledReason(inputs),/historical/);
  assert.equal(checkDisabledReason(inputs,'',true),'');
  assert.equal(checkDisabledReason(inputs,'',false,known),'');
  assert.equal(inputs.task4_split,null);
  assert.deepEqual(savedTask4Profile(d),{split:400,overlap:100,project_id:12,task_id:'exact-task'});
  assert.equal(savedTask4Profile({...d,operations:[{operation_key:'task4',options:{split:'broken'}}]}),null);
});
test('latest successful attempt supersedes old failure; later forced failure remains authoritative',()=>{
  const d=detail();d.run.status='completed';
  d.stages=[{id:1,stage_name:'qgis',status:'failed'},{id:2,stage_name:'qgis',status:'completed'}];
  assert.deepEqual(latestStages(d.stages).map(row=>row.id),[2]);
  assert.equal(runPresentation(d).status,'completed');
  d.stages.push({id:3,stage_name:'qgis',status:'failed'});
  assert.equal(runPresentation(d).status,'failed');
  assert.equal(historyStatus({status:'completed',latest_stage:{status:'failed'}}),'failed');
  assert.equal(historyStatus({status:'completed',latest_stage:{status:'completed'}}),'completed');
});
test('finished local pause and failure receipts do not claim active processing or completion',()=>{
  const d=detail();d.run.status='completed';d.stages=[{id:1,stage_name:'qgis',status:'completed'}];
  d.console_operations=[{kind:'run',state:'finished',result:{pipeline:{paused:true}}}];
  assert.equal(runPresentation(d).status,'paused');
  d.console_operations[0]={kind:'run',state:'failed',result:{pipeline:{success:false}}};
  assert.equal(runPresentation(d).status,'failed');
});
test('resumed run ignores old operation recovery but honors newest uncertain ownership',()=>{
  const d=detail();d.stages=[{id:4,stage_name:'webodm_task4',status:'running'}];
  d.console_operations=[{kind:'cli',created_at:'2026-01-01',state:'requires_recovery'},{kind:'run',created_at:'2026-01-02',state:'running'}];
  assert.equal(runPresentation(d).localReview,false);
  assert.equal(runPresentation(d).status,'running');
  d.console_operations.push({kind:'run',created_at:'2026-01-03',state:'requires_recovery'});
  assert.equal(runPresentation(d).localReview,true);
  assert.match(runPresentation(d).next,/Technical/);
});
test('remote failure is guidance from last observation and does not invent remote success',()=>{
  const d=detail();d.run.status='paused';d.operations=[{remote_status:'failed'}];
  assert.match(runPresentation(d).next,/last observed/);
  assert.equal(runPresentation(d).status,'paused');
  assert.equal(statusLabel(null),'Not recorded');
  assert.equal(stageLabel('webodm_task4'),'Make orthomosaic (Task 4)');
  assert.equal(statusTone('requires_recovery'),'error');
});
test('duration formatting never prints raw floats or invented values',()=>{
  assert.equal(duration(1528.595733),'25m 29s');assert.equal(duration(3661),'1h 1m 1s');
  assert.equal(duration(0),'0s');for(const value of [null,undefined,NaN,-1,'bad'])assert.equal(duration(value),'Not recorded');
});
test('tabs support arrow keys, Home and End without changing on other keys',()=>{
  assert.equal(tabForKey('progress','ArrowRight'),'outputs');assert.equal(tabForKey('progress','ArrowLeft'),'technical');
  assert.equal(tabForKey('technical','Home'),'progress');assert.equal(tabForKey('progress','End'),'technical');
  assert.equal(tabForKey('outputs','Tab'),null);
});
test('overview supersedes old invocations and does not promote historical failed preflights',()=>{
  const rows=[{operation_id:'old',run_id:'run',kind:'cli',created_at:'2026-01-01',state:'requires_recovery'},
    {operation_id:'new',run_id:'run',kind:'run',created_at:'2026-01-02',state:'running'}];
  assert.deepEqual(overviewOperations(rows).map(row=>row.operation_id),['new']);
  assert.equal(operationAttention({kind:'preflight',state:'failed'}),false);
  assert.equal(operationAttention({kind:'preflight',state:'requires_recovery'}),true);
});

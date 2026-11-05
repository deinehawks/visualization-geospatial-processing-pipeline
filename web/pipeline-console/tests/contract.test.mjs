import test from 'node:test';
import assert from 'node:assert/strict';
import {newInputs,resumeInputs,passing,busy,tileConfirmation,outcome,changedInput,canDispatch,legacyDisposition} from '../lib/contract.mjs';
test('server capacity permits a second run but not a third or an uncertain owner',()=>{assert.equal(canDispatch({max_runs:2,occupied:1,available:1,blocked:false}),true);assert.equal(canDispatch({max_runs:2,occupied:2,available:0,blocked:false}),false);assert.equal(canDispatch({available:1,blocked:true}),false);assert.equal(canDispatch(null),false);});
test('maintenance and legacy execution remain exclusive',()=>{const free={max_runs:2,occupied:1,available:1,blocked:false};assert.equal(canDispatch(free,[],true),false);assert.equal(canDispatch({...free,occupied:0},[],true),true);assert.equal(canDispatch(free,['unverified']),false);});
test('new defaults preserve Task 4 and CLI options',()=>{const v=newInputs();assert.equal(v.mode,'task4');assert.equal(v.rgb,false);assert.equal(v.disable_cross_run,false);assert.equal(v.keep_workspace,false);assert.equal(v.run_id,null);});
test('resume preserves identity and does not guess unknown mode',()=>{const d={run:{run_id:'original',survey_id:'AH-026136',year:2026,source_dir:'test/source'},mode:null};const v=resumeInputs(d);assert.equal(v.run_id,'original');assert.equal(v.mode,null);assert.equal(v.survey,'test/source');assert.equal(v.action,'resume');assert.equal(resumeInputs(d,true).action,'rebuild_tiles');});
test('field changes invalidate preflight',()=>{for(const [k,v] of Object.entries(newInputs()))assert.equal(changedInput(newInputs(),k,v).preflight,null);});
test('only finished structured preflight permits start',()=>{assert.equal(passing({kind:'preflight',state:'finished',result:{preflight:{report:{ok:true}}}}),true);for(const v of [null,{kind:'run',state:'finished'},{kind:'preflight',state:'failed'},{kind:'preflight',state:'finished'}])assert.equal(!!passing(v),false);});
test('unknown operations occupy the slot; exit labels do not invent success',()=>{assert.equal(busy({state:'requires_recovery'}),true);assert.equal(busy({state:'accepted'}),true);assert.equal(busy({state:'finished'}),false);assert.equal(outcome({state:'finished'}),'finished');assert.equal(outcome({state:'finished',result:{pipeline:{success:false}}}),'Failed');assert.equal(outcome({result:{pipeline:{paused:true}}}),'Paused locally — WebODM may continue');});
test('tile authorization binds task and run',()=>assert.equal(tileConfirmation({run_id:'original',rebuild_task:'task4'}),'REBUILD QGIS TILES task4 FOR RUN original'));
test('resume carries recorded advanced settings and not survey wording',()=>{const d={run:{run_id:'old',survey_id:'AH-026136',year:2026},mode:'task2',resume_options:{rgb:true,node_id:7,keep_workspace:true}};const v=resumeInputs(d);assert.equal(v.rgb,true);assert.equal(v.node_id,7);assert.equal(v.keep_workspace,true);assert.equal(v.action,'resume');});
test('finished runner with owned descendants still occupies UI slot',()=>assert.equal(busy({state:'finished',resource_busy:true}),true));

test('local legacy acknowledgment never claims remote success or changes persisted status',()=>{
  assert.equal(legacyDisposition('old',{}),'');
  assert.equal(legacyDisposition('old',null),'');
  const value={legacy_reconciled:[{run_id:'old',persisted_status:'running',remote_status:'unverified'}]};
  assert.equal(legacyDisposition('other',value),'');
  assert.equal(legacyDisposition('old',value),'Locally reconciled; WebODM unverified');
  assert.equal(value.legacy_reconciled[0].persisted_status,'running');
});

// Explicit fixture-only visual preview. No production imports, credentials or service calls.
// Run after the static build: node tests/preview.mjs; open http://127.0.0.1:8877/?scenario=running
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,sep,extname} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('../out/',import.meta.url)));
const allowed=new Set(['running','paused','interrupted','remote-failed','completed','empty']);
let scenario='running';
let checks=[];
const requests=[];
const stamp='2026-11-13T03:00:00+00:00';
const runId='fixture-run-001';
function fixture() {
  const state=scenario==='interrupted'?'requires_recovery':scenario==='remote-failed'?'failed':scenario==='paused'?'paused':scenario==='completed'?'completed':'running';
  const run={run_id:runId,survey_id:'DEMO-001',year:2026,status:state,source_dir:'C:/fixture-only/field-data/demo-survey',started_at:stamp};
  const stages=[
    {id:1,stage_name:'data_segregation',status:'completed',runtime_seconds:1528.5957,authoritative_attempt:true,attempt_number:1},
    {id:2,stage_name:'cross_run_filter',status:'completed',runtime_seconds:42,authoritative_attempt:true,attempt_number:1},
    {id:3,stage_name:'kml_boundary',status:'completed',runtime_seconds:.3,authoritative_attempt:true,attempt_number:1},
    {id:4,stage_name:'webodm_task4',status:'requires_recovery',runtime_seconds:3909.13,error_message:'Fixture historical failure retained for diagnosis.',attempt_number:1,authoritative_attempt:false},
    {id:5,stage_name:'webodm_task4',status:state==='requires_recovery'?'paused':state,runtime_seconds:3661,attempt_number:2,authoritative_attempt:true,error_message:scenario==='remote-failed'?'Fixture remote processing failure: inspect the exact task.':null}
  ];
  if(scenario==='completed')stages.push({id:6,stage_name:'qgis',status:'completed',runtime_seconds:321,attempt_number:1,authoritative_attempt:true});
  const operation={operation_id:'fixture-operation-current',run_id:runId,kind:'run',state:scenario==='paused'||scenario==='completed'?'finished':state,created_at:stamp,
    observed_state:scenario==='interrupted'?'requires_reconciliation':null,controls_available:scenario==='running',log_tail:'Fixture only: Task 4 processing. No service was contacted.',result:scenario==='paused'?{pipeline:{paused:true}}:scenario==='completed'?{pipeline:{success:true}}:undefined};
  const old={operation_id:'fixture-operation-old',run_id:runId,kind:'cli',state:'resolved',created_at:'2026-11-12T03:00:00+00:00',controls_available:false};
  const operations=scenario==='empty'?[]:[operation,old];
  const task={id:1,operation_key:'task4',project_id:900,task_id:'fixture-task-uuid',local_status:'reattached',remote_status:scenario==='remote-failed'?'failed':scenario==='completed'?'completed':'running',options:{split:400,'split-overlap':100}};
  run.latest_stage=stages.at(-1);
  return {run,operations,detail:{run,stages,console_operations:operations,operations:[task],mode:'task4',resume_options:{},resume_known_options:[],webodm_project_links:[{project_id:900,url:'#fixture-project'}],artifacts:scenario==='completed'?[{logical_name:'qgis.clipped_orthomosaic',path:'C:/fixture-only/outputs/orthomosaic-clipped.tif'}]:[]}};
}
const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
const server=http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,'http://127.0.0.1:8877');
    if(url.pathname==='/'&&allowed.has(url.searchParams.get('scenario'))){scenario=url.searchParams.get('scenario');checks=[];}
    if(url.pathname.startsWith('/api/')) {
      const data=fixture();
      if(req.method==='POST') {
        let body='';for await(const part of req){body+=part;if(body.length>100000)throw Error('Fixture input too large');}
        const value=JSON.parse(body||'{}');requests.push({path:url.pathname,body:value});
        if(url.pathname==='/api/preflight'){
          const input=value.inputs;
          const ok=!input.survey.includes('storage-failure');
          const profile=input.task4_split?{split:input.task4_split,split_overlap_meters:input.task4_split_overlap??100,processing_node:input.node_id,approximate_base_submodels:11}:null;
          const check={operation_id:value.command_id,kind:'preflight',state:'finished',created_at:new Date().toISOString(),run_id:input.run_id||'fixture-new-run',result:{preflight:{source_dir:input.survey,report:{ok,source_image_count:4288,volumes:[{volume:'Fixture output volume',roles:['published outputs'],free_bytes:(ok?150:10)*1073741824,required_bytes:80*1073741824,reserve_bytes:5*1073741824,ok}]},task4_split:profile}}};checks.unshift(check);return json(res,check);
        }
        if(url.pathname==='/api/start')return json(res,{operation_id:value.command_id,kind:'run',state:'accepted',run_id:'fixture-new-run'});
        if(url.pathname.endsWith('/reconcile')){scenario='paused';return json(res,{state:'resolved'});}
        return json(res,{state:'requested'});
      }
      if(url.pathname==='/api/session')return json(res,{csrf:'fixture-only'});
      if(url.pathname==='/api/runs')return json(res,{items:scenario==='empty'?[]:[data.run],next_cursor:null,availability:'available'});
      if(url.pathname==='/api/operations')return json(res,{items:[...checks,...data.operations],legacy_running:[],execution:{max_runs:2,occupied:scenario==='running'||scenario==='interrupted'?1:0,available:scenario==='interrupted'?0:scenario==='running'?1:2,blocked:scenario==='interrupted'}});
      if(url.pathname.endsWith('/logs'))return json(res,{items:[{message:'Fixture log line: no real processing.'}]});
      return json(res,data.detail);
    }
    if(url.pathname==='/fixture-requests')return json(res,{scenario,requests});
    const name=resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));
    if(!name.startsWith(root+sep))return json(res,{error:'Outside static preview'},403);
    const body=await readFile(name);
    res.writeHead(200,{'Content-Type':({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.ico':'image/x-icon'})[extname(name)]||'application/octet-stream','Cache-Control':'no-store'});res.end(body);
  } catch(error) {json(res,{error:String(error)},404);}
});
server.listen(8877,'127.0.0.1',()=>process.stdout.write('Fixture-only console preview: http://127.0.0.1:8877/?scenario=running\n'));

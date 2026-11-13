import {ReactNode, useEffect, useId, useRef, useState} from 'react';
import {statusLabel,statusTone,tabForKey} from '../lib/contract.mjs';

export function StatusBadge({status,label}:{status?:string;label?:string}) {
  return <span className={'badge '+statusTone(status)}><span className="status-dot"/>{label || statusLabel(status)}</span>;
}
export function Panel({title,description,children,action,className=''}:{title:string;description?:string;children:ReactNode;action?:ReactNode;className?:string}) {
  return <section className={'card '+className}><div className="section-heading"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</div>{children}</section>;
}
export function Empty({title,children}:{title:string;children?:ReactNode}) {
  return <div className="empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}
export function Tabs({value,onChange,children}:{value:string;onChange:(value:string)=>void;children:ReactNode}) {
  const id=useId();
  const refs=useRef<Record<string,HTMLButtonElement|null>>({});
  return <><div role="tablist" aria-label="Run workspace sections" className="tabs">
    {['progress','outputs','technical'].map(name=><button key={name} ref={el=>{refs.current[name]=el;}} id={id+'-'+name} role="tab" type="button" aria-selected={value===name} aria-controls={id+'-panel-'+name} tabIndex={value===name?0:-1} onClick={()=>onChange(name)} onKeyDown={event=>{const next=tabForKey(name,event.key);if(next){event.preventDefault();onChange(next);refs.current[next]?.focus();}}}>{name==='technical'?'Technical details':name==='progress'?'Progress':'Outputs'}</button>)}
  </div>{['progress','outputs','technical'].map(name=><div key={name} hidden={value!==name} role="tabpanel" id={id+'-panel-'+name} aria-labelledby={id+'-'+name} tabIndex={0}>{value===name?children:null}</div>)}</>;
}

export function ConfirmationDialog({title,body,phrase,onAccept,onCancel}:{title:string;body:string;phrase?:string;onAccept:(phrase:string)=>void;onCancel:()=>void}) {
  const ref=useRef<HTMLDialogElement>(null);
  const [typed,setTyped]=useState('');
  const id=useId();
  useEffect(()=>{ref.current?.showModal();},[]);
  return <dialog ref={ref} aria-labelledby={id} onCancel={event=>{event.preventDefault();onCancel();}}><h2 id={id}>{title}</h2><p>{body}</p>{phrase&&<label>Type this exact confirmation<code>{phrase}</code><input autoComplete="off" value={typed} onChange={event=>setTyped(event.target.value)}/></label>}<div className="button-row dialog-actions"><button autoFocus onClick={onCancel}>Cancel</button><button className="primary" disabled={!!phrase&&typed!==phrase} onClick={()=>onAccept(typed)}>Confirm</button></div></dialog>;
}

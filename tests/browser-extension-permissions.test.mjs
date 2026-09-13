import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

const background = readFileSync(new URL('../browser-extension/background.js',import.meta.url),'utf8');
const popupSource = readFileSync(new URL('../browser-extension/popup.js',import.meta.url),'utf8');
const origin = `chrome-extension://${'a'.repeat(32)}/`;
const selected = {id:7,windowId:2,url:'http://127.0.0.1:43123/index.html',title:'Local acceptance',status:'complete',incognito:false};
const flush = async () => { for (let n=0;n<12;n++) await new Promise(resolve=>setImmediate(resolve)); };
function worker(initial = {}) {
  const events={}, saved=structuredClone(initial), requests=[], injections=[], posts=[], timers=new Map();
  let tab={...selected}, allowed=false, now=100000, timerId=0, gesture=false, alive=true, getHook=null;
  const event=name=>({addListener(fn){events[name]=fn;}});
  const available=()=>{ if (!alive) throw Error('Extension context invalidated'); };
  class Clock extends Date { static now(){return now;} }
  const chrome={storage:{local:{async get(){available();return structuredClone(saved);},async set(value){available();Object.assign(saved,structuredClone(value));}}},
    runtime:{id:'a'.repeat(32),getURL:path=>origin+path,onMessage:event('message'),onStartup:event('startup'),onInstalled:event('installed'),
      connectNative(){available();return {onMessage:event('nativeMessage'),onDisconnect:event('disconnect'),disconnect(){},postMessage(message){
        posts.push(structuredClone(message)); if(message.type==='hello')queueMicrotask(()=>events.nativeMessage({type:'ack',operation:'hello',sessionId:'native-session-12345678'}));
      }};}},
    permissions:{async contains(){available();return allowed;},request(details){
      available();assert.equal(gesture,true,'permissions.request must run in the synchronous user-gesture callback');
      let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
      requests.push({details:structuredClone(details),resolve,reject});return promise;
    },onRemoved:event('revoked')},
    tabs:{async get(id){available();if(getHook)await getHook();if(!tab||id!==tab.id)throw Error('closed');return {...tab};},
      onRemoved:event('removed'),onUpdated:event('updated'),onReplaced:event('replaced')},
    scripting:{async executeScript(value){available();injections.push(value);}},
    alarms:{onAlarm:event('alarm'),async create(){}},windows:{async update(){throw Error('No focus expected');}}};
  const context=vm.createContext({chrome,URL,crypto:webcrypto,Date:Clock,queueMicrotask,
    setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,at:now+ms});return id;},clearTimeout(id){timers.delete(id);},
    setInterval(){return 0;},clearInterval(){}});
  vm.runInContext(background,context);
  const send=(message,sender={url:origin+'popup.html'},respond)=>{
    available();events.message(message,sender,respond);
  };
  const call=message=>new Promise(resolve=>send(message,undefined,value=>resolve(structuredClone(value))));
  const click=(message={type:'requestTrack',tabId:selected.id,windowId:selected.windowId,url:selected.url})=>{
    gesture=true;try{return call(message);}finally{gesture=false;}
  };
  const grant=async (value=true,index=requests.length-1)=>{allowed=value;requests[index].resolve(value);await flush();};
  function popup() {
    let popupAlive=true;const pageEvents={},elements=new Map();
    class Element {
      constructor(){this.handlers={};this.children=[];this.hidden=false;this.textContent='';}
      addEventListener(type,fn){this.handlers[type]=fn;}
      append(...nodes){this.children.push(...nodes);}
      replaceChildren(...nodes){this.children=nodes;}
      contains(){return false;}
    }
    const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
    const popupChrome={tabs:{async query(){return [{...tab}];}},runtime:{id:chrome.runtime.id,sendMessage(message){
      if(!popupAlive)throw Error('Extension context invalidated');
      return new Promise(resolve=>send(message,undefined,value=>{
        if(!popupAlive)throw Error('Popup response channel destroyed');resolve(structuredClone(value));
      }));
    }},permissions:{request(){throw Error('Permission must be owned by the worker, not the disposable popup');}}};
    const popupContext=vm.createContext({chrome:popupChrome,URL,document:{hidden:false,activeElement:null,getElementById:element,createElement:()=>new Element()},
      navigator:{clipboard:{async writeText(){}}},setInterval(){return 0;},clearInterval(){},addEventListener(type,fn){pageEvents[type]=fn;}});
    vm.runInContext(popupSource,popupContext);
    return {element,click(){gesture=true;try{return element('track').handlers.click();}finally{gesture=false;}},
      destroy(){popupAlive=false;pageEvents.pagehide?.();}};
  }
  return {events,saved,requests,injections,posts,call,click,grant,popup,
    async advance(ms){now+=ms;for(const [id,timer]of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}await flush();},
    set tab(value){tab=value;},get tab(){return tab;},set allowed(value){allowed=value;},
    set getHook(value){getHook=value;},dispose(){alive=false;timers.clear();},
    async state(){return call({type:'state'});}};
}
async function connectedWorker(initial) {const w=worker(initial);await flush();assert.equal((await w.state()).connected,true);return w;}

test('actual popup script can disappear during native grant without losing its exact selection',async()=>{
  const w=await connectedWorker(), popup=w.popup();await flush();
  popup.click();assert.equal(w.requests.length,1);popup.destroy();
  assert.equal((await w.state()).pendingTrack.tabId,7);assert.equal(w.injections.length,0);
  await w.grant();const state=await w.state();
  assert.equal(state.pendingTrack,null);assert.equal(state.sources.length,1);
  assert.deepEqual([state.sources[0].tabId,state.sources[0].windowId,state.sources[0].url],[7,2,selected.url]);
  assert.ok(w.injections.length>0);assert.ok(w.injections.every(item=>item.target.tabId===7&&item.target.frameIds[0]===0));
  assert.deepEqual(w.requests[0].details,{origins:['http://127.0.0.1:43123/*']});
  assert.equal(Object.hasOwn(w.saved,'pendingTrack'),false);w.dispose();
});

test('denial after popup destruction cannot be revived by a later unrelated grant',async()=>{
  const w=await connectedWorker(), popup=w.popup();await flush();popup.click();popup.destroy();
  await w.grant(false);assert.equal((await w.state()).pendingTrack,null);
  w.allowed=true;await flush();assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
});

test('explicit cancellation invalidates the original request even if its native prompt later grants',async()=>{
  const w=await connectedWorker(), result=w.click();const pending=(await w.state()).pendingTrack;
  await w.call({type:'cancelTrackRequest',requestId:pending.id});await w.grant(true);
  assert.equal((await result).ok,false);assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
});

test('timeout does not leave an intent that can track after a late native grant',async()=>{
  const w=await connectedWorker(), result=w.click();await w.advance(60001);await w.grant();
  assert.equal((await result).ok,false);assert.equal((await w.state()).pendingTrack,null);assert.equal((await w.state()).sources.length,0);w.dispose();
});

test('page navigation then returning to the same URL still cancels the original intent',async()=>{
  const w=await connectedWorker(), result=w.click();
  w.tab={...selected,url:'http://127.0.0.1:43123/second.html'};await w.events.updated(7,{url:w.tab.url});
  w.tab={...selected};await w.events.updated(7,{url:selected.url});await w.grant();
  assert.equal((await result).ok,false);assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
});

test('same-URL reload and closed/replaced tabs cannot be adopted after authorization',async()=>{
  for(const invalidate of [w=>w.events.updated(7,{status:'loading'}),w=>{w.tab=null;return w.events.removed(7);},w=>w.events.replaced(8,7)]){
    const w=await connectedWorker(), result=w.click();await invalidate(w);await w.grant();
    assert.equal((await result).ok,false);assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
  }
});

test('final binding rejects changed window, URL, incognito or suspended target even without an event',async()=>{
  for(const patch of [{windowId:9},{url:'https://another.example/page'},{incognito:true},{discarded:true},{frozen:true}]){
    const w=await connectedWorker(), result=w.click();w.tab={...selected,...patch};await w.grant();
    assert.equal((await result).ok,false);assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
  }
});

test('permission removed while tab revalidation is awaiting prevents final binding',async()=>{
  const w=await connectedWorker(), result=w.click();let checks=0;
  w.getHook=async()=>{if(++checks===2){w.allowed=false;await w.events.revoked();}};
  await w.grant();assert.equal((await result).ok,false);assert.equal((await w.state()).sources.length,0);assert.equal(w.injections.length,0);w.dispose();
});

test('only one bounded intent exists and a web page cannot start native permission requests',async()=>{
  const w=await connectedWorker(), result=w.click();
  assert.equal((await w.click()).ok,false);assert.equal(w.requests.length,1);
  const denied=await new Promise(resolve=>w.events.message({type:'requestTrack',tabId:7,windowId:2,url:selected.url},
    {url:selected.url,frameId:0,tab:{id:7}},resolve));
  assert.equal(denied.ok,false);assert.equal(w.requests.length,1);
  await w.grant(false);await result;w.dispose();
});

test('worker restart does not restore a pending selection or treat existing site permission as a selection',async()=>{
  const original=await connectedWorker();original.click();await flush();const saved=structuredClone(original.saved);original.dispose();
  const restarted=await connectedWorker(saved);restarted.allowed=true;await original.grant();await flush();
  const state=await restarted.state();assert.equal(state.pendingTrack,null);assert.equal(state.sources.length,0);
  assert.equal(restarted.requests.length,0);assert.equal(restarted.injections.length,0);restarted.dispose();
});

test('permission request cannot silently bypass a missing Chrome user gesture',async()=>{
  const w=await connectedWorker();const result=await w.call({type:'requestTrack',tabId:7,windowId:2,url:selected.url});
  assert.equal(result.ok,false);assert.equal((await w.state()).sources.length,0);assert.equal((await w.state()).pendingTrack,null);w.dispose();
});

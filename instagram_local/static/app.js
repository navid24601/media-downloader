'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
if (fragment) { sessionStorage.setItem('app-token', fragment); history.replaceState(null, '', '/'); }
const token = sessionStorage.getItem('app-token') || '';
let importData = null, historyBefore = null, historyRows = [], initialized = false;
function youtubeOptions(){return {yt_mode:$('yt-mode').value,yt_quality:$('yt-quality').value,yt_scope:$('yt-scope').value,yt_start:$('yt-start').value,yt_end:$('yt-end').value,yt_subtitles:$('yt-subtitles').checked,yt_thumbnail:$('yt-thumbnail').checked};}
$('yt-mode').addEventListener('change',()=>{$('yt-quality').disabled=$('yt-mode').value==='mp3';});
const youtubeStatus=node('p');youtubeStatus.id='youtube-auth';
const youtubeHeading=node('h3','YouTube session (optional)');
const youtubeHelp=node('p','Public videos need no session. Import a local YouTube Netscape or JSON cookie export only for content your account can view. See README for safe export instructions.');
const youtubeFile=node('input');youtubeFile.type='file';youtubeFile.accept='.txt,.json';youtubeFile.id='youtube-cookie-file';youtubeFile.setAttribute('aria-label','YouTube cookie export');
const youtubeImport=node('button','Import YouTube cookies');youtubeImport.id='youtube-import';
const youtubeClear=node('button','Clear YouTube session');youtubeClear.id='youtube-disconnect';
$('disconnect').closest('div').after(youtubeHeading,youtubeHelp,youtubeStatus,youtubeFile,youtubeImport,youtubeClear);
function notice(message, error=false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); }
async function api(path, body={}) {
  const res = await fetch('/api/'+path, {method:'POST', headers:{'Content-Type':'application/json','X-App-Token':token}, body:JSON.stringify(body)});
  const data = await res.json(); if (!res.ok) throw new Error(data.error || 'Local request failed'); return data;
}
function action(id, fn) { $(id).addEventListener('click', async () => { $(id).disabled=true; try { const result=await fn(); if(result?.message) notice(result.message); if(id!=='shutdown')await refresh(); } catch(e) { notice(e.message,true); } finally { $(id).disabled=false; } }); }
function node(tag,text,className) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(className)n.className=className; return n; }
function link(url) { const a=node('a',url); a.href=url; a.target='_blank'; a.rel='noreferrer noopener'; return a; }
function table(target, headers, rows) { const t=node('table'), head=node('tr'); headers.forEach(x=>head.append(node('th',x))); t.append(head); rows.forEach(cells=>{ const tr=node('tr'); cells.forEach(c=>{const td=node('td'); td.append(c instanceof Node?c:document.createTextNode(String(c??'')));tr.append(td);});t.append(tr);}); $(target).replaceChildren(t); if(!rows.length)$(target).append(node('p','No items yet.','hint')); }
function renderHistory(rows) { table('history',['Time','Original link','Output file','Status'],rows.map(r=>[new Date(r.updated).toLocaleString(),link(r.url),r.output.split(/[\\/]/).pop(),r.error||r.status.replaceAll('_',' ')])); $('older-history').disabled=rows.length<50; }
async function refresh() {
  if($('downloader-view').hidden)return;
  const s=await api('state');
  if(!initialized) { $('folder').value=s.settings.folder; $('caption').checked=s.settings.caption; initialized=true; }
  $('auth-status').textContent=s.settings.session?(s.settings.session_verified?'Saved access verified':'Session imported · not yet verified'):'Public access · no session';
  $('youtube-auth').textContent=s.settings.youtube_session?'YouTube session imported locally.':'YouTube public access · no session';
  $('saved-auth').textContent=s.settings.session?'Uses your imported account session.':'Import your session in Connection & settings before using Saved.';
  $('proxy-status').textContent=s.settings.proxy_set?'An explicit proxy is saved securely. Its address and credentials are not displayed.':'No explicit proxy. Uses VPN/TUN or detected static system/environment proxy.';
  $('counts').textContent=Object.entries(s.counts).map(([k,v])=>`${v} ${k}`).join(' · ');
  $('worker-message').textContent=s.worker.message;
  $('resume').disabled=s.worker.active; $('pause').disabled=!s.worker.active;
  const p=s.worker.progress; $('progress-wrap').hidden=!p;
  if(p) { $('progress-label').textContent=p.label+(p.total?` · ${(p.done/1048576).toFixed(1)} / ${(p.total/1048576).toFixed(1)} MB`:''); if(p.total){$('progress').max=p.total;$('progress').value=p.done;}else{$('progress').removeAttribute('value');} }
  $('sources').replaceChildren(...s.sources.filter(r=>r.status!=='done'&&r.status!=='canceled').map(r=>node('div',`${r.label} · ${r.emitted}${r.maximum&&r.kind!=='youtube_playlist'?' / '+r.maximum:''} entries discovered${r.kind==='youtube_playlist'?' · total unknown':''} · ${r.status}${r.error?' · '+r.error:''}`,'source')));
  table('queue',['Item','Status','Details'],s.queue.map(r=>[link(r.url),node('span',r.status,'status '+r.status),r.error||r.description||(r.status==='running'?`Attempt ${r.attempts}/3`:'')]));
  if(historyBefore===null)renderHistory(s.history);
}
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{ document.querySelectorAll('[data-mode]').forEach(x=>{x.classList.toggle('selected',x===b);$(x.dataset.mode+'-panel').hidden=x!==b;}); }));
for(const prefix of ['profile','saved'])$(prefix+'-limit').addEventListener('change',()=>{const all=$(prefix+'-limit').value==='all';$(prefix+'-n').hidden=all;if(prefix==='saved')$('order-wrap').hidden=all;});
$('saved-scope').addEventListener('change',()=>{$('collection-wrap').hidden=$('saved-scope').value!=='collection';});
function errors(result){$('input-errors').replaceChildren(...(result.errors||[]).map(e=>node('p',`Line ${e.line}: ${e.error}`)));if(result.invalid_count>100)$('input-errors').append(node('p',`${result.invalid_count} invalid links; showing the first 100.`));}
action('add-links',async()=>{const r=await api('links',{values:$('links').value.split(/\r?\n/),again:$('again').checked,...youtubeOptions()});errors(r);return r;});
$('import-file').addEventListener('change',()=>{importData=null;$('column-wrap').hidden=true;});
action('add-file',async()=>{
  const f=$('import-file').files[0]; if(!f)throw new Error('Choose a TXT or CSV file');if(f.size>4000000)throw new Error('Import limit is 4 MB');
  if(!importData)importData={text:await f.text(),filename:f.name};
  const body={...importData};if(!$('column-wrap').hidden)body.column=Number($('column').value);
  const parsed=await api('import',body);
  if(parsed.columns){$('column').replaceChildren(...parsed.columns.map(c=>{const o=node('option',c.name);o.value=c.index;return o;}));$('column-wrap').hidden=false;notice('Multiple possible columns. Choose one, then click Import to queue.');return;}
  const r=await api('links',{values:parsed.values,again:$('again').checked,source:'file',...youtubeOptions()}); errors(r);return r;
});
action('add-profile',()=>api('source',{kind:'profile',target:$('profile').value,maximum:$('profile-limit').value==='all'?null:Number($('profile-n').value),again:$('again').checked}));
action('add-saved',()=>api('source',{kind:$('saved-scope').value,target:$('collection').value,maximum:$('saved-limit').value==='all'?null:Number($('saved-n').value),accept_available_order:$('accept-order').checked,again:$('again').checked}));
for(const id of ['resume','pause','cancel','retry','test','session-test','disconnect'])action(id,()=>api(id));
action('youtube-disconnect',()=>api('youtube-disconnect'));
action('youtube-import',async()=>{const f=$('youtube-cookie-file').files[0];if(!f)throw new Error('Choose a YouTube cookie export');if(f.size>2000000)throw new Error('Cookie import is too large');const r=await api('youtube-cookies',{text:await f.text()});$('youtube-cookie-file').value='';return r;});
action('save-settings',()=>api('settings',{folder:$('folder').value,caption:$('caption').checked}));
action('save-proxy',async()=>{if(!$('proxy').value.trim())throw new Error('Enter a proxy, or use Clear explicit proxy');const r=await api('proxy',{proxy:$('proxy').value});$('proxy').value='';return r;});
action('clear-proxy',()=>api('proxy',{proxy:''}));
action('import-cookies',async()=>{const f=$('cookies-file').files[0];if(!f)throw new Error('Choose an Instagram cookie export');if(f.size>2000000)throw new Error('Cookie import is too large');const r=await api('cookies',{text:await f.text()});$('cookies-file').value='';return r;});
action('refresh-history',async()=>{historyBefore=null;await refresh();});
action('older-history',async()=>{const current=await api('state',{before:historyBefore});if(!current.history.length)return;historyBefore=current.history.at(-1).id;const older=await api('state',{before:historyBefore});renderHistory(older.history);});
action('shutdown',async()=>{const r=await api('shutdown');clearInterval(poll);notice(r.message);return r;});
const poll=setInterval(()=>refresh().catch(e=>notice(e.message||'Server disconnected. Launch the app again to continue.',true)),1500);
refresh().then(()=>notice('Ready. Add downloads, then choose Resume.')).catch(e=>notice(e.message,true));

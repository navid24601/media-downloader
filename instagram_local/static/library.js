'use strict';
// The downloader DOM stays mounted. All Library traffic is to local endpoints.
const libraryState = {rows:[], cursors:[null], page:0, next:null, request:0, viewer:null, slide:0, scroll:0, loaded:false, deletePlan:null};
let librarySearchTimer, thumbnailTimer;
const fetchDetailsButton=node('button','Fetch missing details…');fetchDetailsButton.id='viewer-fetch';$('viewer-delete').before(fetchDetailsButton);
const detailsError=node('p');detailsError.id='details-error';detailsError.setAttribute('role','status');$('details-queue').parentElement.before(detailsError);
$('caption').closest('label').after(node('p','Captions are also retained in Library metadata, independently of this text-file option.','hint'));
function libraryApi(action,body={}) { return api('library/'+action,body); }
async function selectWorkspace(name) {
  const isLibrary=name==='library';
  $('downloader-view').hidden=isLibrary;$('library-view').hidden=!isLibrary;
  for(const tab of ['downloader','library']) { const selected=tab===name;$(tab+'-tab').classList.toggle('selected',selected);$(tab+'-tab').setAttribute('aria-selected',String(selected)); }
  if(isLibrary) {
    try { await libraryApi('access'); if(!libraryState.loaded) {await loadLibraryFilters();await loadLibrary(true);} else await loadLibrary(); notice('Offline Library. Your downloader continues independently.'); }
    catch(e) {notice(e.message,true);}
  } else {clearTimeout(thumbnailTimer);await refresh();notice('Downloader ready. Your inputs are preserved.');}
}
$('downloader-tab').addEventListener('click',()=>selectWorkspace('downloader'));
$('library-tab').addEventListener('click',()=>selectWorkspace('library'));
async function loadLibraryFilters() {
  const data=await libraryApi('filters');
  for(const [id,values,label] of [['library-user',data.usernames,'All accounts'],['library-source',data.sources,'All sources']]) {
    const selected=$(id).value;
    const first=node('option',label);first.value='';
    $(id).replaceChildren(first,...values.map(value=>{const option=node('option',value);option.value=value;return option;}));
    if(values.includes(selected))$(id).value=selected;
  }
  const selected=$('library-playlist').value,first=node('option','All playlists');first.value='';
  $('library-playlist').replaceChildren(first,...data.playlists.map(p=>{const option=node('option',p.title);option.value=p.id;return option;}));
  $('library-playlist').value=selected;
}
async function loadLibrary(reset=false) {
  if(reset){libraryState.page=0;libraryState.cursors=[null];}
  const serial=++libraryState.request;
  $('library-status').textContent='Reading local index…';
  const data=await libraryApi('list',{query:$('library-search').value,username:$('library-user').value,
    source:$('library-source').value,platform:$('library-platform').value,playlist:$('library-playlist').value,favorites:$('library-favorites').checked,hidden:$('library-hidden').checked,before:libraryState.cursors[libraryState.page]});
  if(serial!==libraryState.request)return;
  libraryState.rows=data.items;libraryState.next=data.next;libraryState.loaded=true;
  renderLibrary();
  $('library-status').textContent=`${data.items.length} posts on this page. ${$('library-playlist').value?'Playlist order.':'Newest downloaded first.'}${data.indexing||data.discovering?' Local indexing is continuing; Refresh to include more files.':''}`;
  if(!data.items.length&&data.discovering)setTimeout(()=>{if(!$('library-view').hidden)loadLibrary().catch(e=>notice(e.message,true));},700);
}
function renderLibrary() {
  $('library-grid').replaceChildren(...libraryState.rows.map((row,index)=>{
    const button=node('button',undefined,'library-card');button.type='button';button.dataset.id=row.id;
    button.setAttribute('aria-label',`Open ${row.title||row.username||'unknown account'} ${row.shortcode||row.filename}`);
    const image=node('img');image.alt='';image.loading='lazy';image.decoding='async';image.src=`/media/${row.id}/thumb`;
    image.dataset.thumb=row.thumb_state;button.append(image);
    if(row.favorite)button.append(node('span','★','card-favorite'));
    button.append(node('span',row.platform==='youtube'?`YouTube · ${row.metadata.mode?.toUpperCase()||'media'}${row.metadata.resolution?' · '+row.metadata.resolution+'p':''}`:row.media_count>1?`${row.media_count} media`:'Instagram','card-badge'));
    button.append(node('span',row.platform==='youtube'?`${row.title||row.shortcode} · ${row.channel||'Unknown channel'} · ${row.download_date}`:row.username?`@${row.username}`:'Unknown account','card-label'));
    if(row.status!=='ready')button.append(node('span',row.status==='pending'?'Indexing…':row.status,'card-error'));
    button.addEventListener('click',()=>openPost(index).catch(e=>notice(e.message,true)));
    image.addEventListener('load',scheduleThumbnails);
    return button;
  }));
  if(!libraryState.rows.length)$('library-grid').append(node('p','No matching posts. Existing files are indexed locally; use Refresh if indexing is still running.','hint'));
  $('library-previous').disabled=libraryState.page===0;$('library-next').disabled=!libraryState.next;
  $('library-page').textContent=`Page ${libraryState.page+1}`;
  scheduleThumbnails();
}
function scheduleThumbnails() {
  clearTimeout(thumbnailTimer);
  if($('library-view').hidden)return;
  thumbnailTimer=setTimeout(async()=>{
    const images=[...$('library-grid').querySelectorAll('img')].filter(image=>image.dataset.thumb==='pending'&&image.complete&&image.getBoundingClientRect().top<innerHeight+300);
    if(!images.length)return;
    try {
      const result=await libraryApi('thumbnails',{ids:images.map(img=>Number(img.parentElement.dataset.id))});
      for(const row of result.items) {
        const image=images.find(img=>Number(img.parentElement.dataset.id)===row.id);
        if(image&&row.state!=='pending') {image.dataset.thumb=row.state;if(row.state==='ready')image.src=`/media/${row.id}/thumb?v=${Date.now()}`;}
      }
      scheduleThumbnails();
    }catch(e){notice(e.message,true);}
  },1200);
}
async function changeLibraryPage(direction) {
  if(direction>0) {if(!libraryState.next)return;libraryState.cursors[libraryState.page+1]=libraryState.next;libraryState.page++;}
  else {if(!libraryState.page)return;libraryState.page--;}
  await loadLibrary();
}
function libraryAction(id,fn) {$(id).addEventListener('click',async()=>{$(id).disabled=true;try{await fn();}catch(e){notice(e.message,true);if($('post-viewer').open)$('viewer-status').textContent=e.message;if($('details-confirm').open)$('details-error').textContent=e.message;}finally{
  $(id).disabled=false;
  if(id==='library-next')$(id).disabled=!libraryState.next;
  if(id==='library-previous')$(id).disabled=libraryState.page===0;
  if(id==='post-previous'&&libraryState.viewer)$(id).disabled=libraryState.viewer.index===0&&libraryState.page===0;
  if(id==='post-next'&&libraryState.viewer)$(id).disabled=libraryState.viewer.index===libraryState.rows.length-1&&!libraryState.next;
}});}
libraryAction('library-refresh',async()=>{await loadLibraryFilters();await loadLibrary(true);});
libraryAction('library-next',()=>changeLibraryPage(1));libraryAction('library-previous',()=>changeLibraryPage(-1));
$('library-search').addEventListener('input',()=>{clearTimeout(librarySearchTimer);librarySearchTimer=setTimeout(()=>loadLibrary(true).catch(e=>notice(e.message,true)),250);});
for(const id of ['library-user','library-source','library-favorites','library-hidden','library-platform','library-playlist'])$(id).addEventListener('change',()=>loadLibrary(true).catch(e=>notice(e.message,true)));
async function openPost(index,keepScroll=false) {
  const summary=libraryState.rows[index];if(!summary)return;
  if(!keepScroll)libraryState.scroll=window.scrollY;
  const data=await libraryApi('detail',{id:summary.id});
  libraryState.viewer={index,...data};libraryState.slide=0;
  renderPost();if(!$('post-viewer').open)$('post-viewer').showModal();
}
function renderPost() {
  const row=libraryState.viewer;
  $('viewer-title').textContent=row.platform==='youtube'?row.title||row.shortcode:row.username?`@${row.username}`:'Unknown account';
  $('viewer-favorite').textContent=row.favorite?'★ Favorited':'☆ Favorite';$('viewer-favorite').setAttribute('aria-pressed',String(Boolean(row.favorite)));
  $('viewer-hide').textContent=row.hidden?'Restore to library':'Hide from library';
  $('viewer-fetch').hidden=row.platform==='youtube'||row.caption!==null&&Boolean(row.username);
  $('viewer-fetch').disabled=!row.url;
  const fields=[['Post',row.url?link(row.url):'Unknown'],['Downloaded',row.download_date||'Unknown'],['Filename',row.filename],['Media',row.media_count||'Unknown'],['Source',row.sources.length?row.sources.map(s=>s.label).join(', '):'Unknown']];
  fields.unshift(['Platform',row.platform==='youtube'?'YouTube':'Instagram']);
  if(row.platform==='youtube') {
    const m=row.metadata;
    fields.push(['Audio',m.audio_language?`${m.audio_language}${m.audio_original?' · original':' · original not identified'}`:'Unknown — language not recorded']);
    fields.push(['Channel',row.channel||'Unknown'],['Video ID',row.shortcode],['Output',m.mode?.toUpperCase()||'Unknown'],['Quality',m.resolution?`${m.resolution}p`:'Audio / unknown'],['Duration',m.duration===null?'Unknown':`${m.duration} seconds`],['Segment',m.start===null?'Full video':`${m.start}–${m.end} seconds${m.segment_precision?' · '+m.segment_precision:''}`],['Playlists',row.playlists.map(p=>`${p.title} (#${p.position})`).join(', ')||'None recorded']);
  }
  if(row.status==='missing')fields.push(['Recorded path',row.output]);
  $('viewer-metadata').replaceChildren(...fields.flatMap(([label,value])=>{const dd=node('dd');dd.append(value instanceof Node?value:document.createTextNode(String(value)));return[node('dt',label),dd];}));
  $('viewer-caption').textContent=row.caption===null?'Unknown — no caption was recorded.':row.caption||'No caption.';
  $('viewer-caption').hidden=row.platform==='youtube';$('viewer-caption').previousElementSibling.hidden=row.platform==='youtube';
  $('viewer-status').textContent=row.error||'';
  $('post-previous').disabled=row.index===0&&libraryState.page===0;
  $('post-next').disabled=row.index===libraryState.rows.length-1&&!libraryState.next;
  renderSlide();
}
function clearPlayback() {for(const video of $('viewer-media').querySelectorAll('video,audio')){video.pause();video.removeAttribute('src');video.load();}$('viewer-media').replaceChildren();}
function renderSlide() {
  clearPlayback();const row=libraryState.viewer,item=row.media[libraryState.slide];
  $('playback-error').textContent='';
  $('slide-position').max=Math.max(row.media.length,1);$('slide-position').value=libraryState.slide+1;
  $('slide-position').disabled=row.media.length<2;
  $('slide-previous').disabled=libraryState.slide<=0;$('slide-next').disabled=libraryState.slide>=row.media.length-1;
  $('slide-label').textContent=item?`${libraryState.slide+1} / ${row.media.length}`:'No media';
  if(!item||row.status!=='ready'){$('viewer-media').append(node('p',row.error||'Local media is unavailable.','hint'));return;}
  const media=node(['video','audio'].includes(item.kind)?item.kind:'img');
  if(['video','audio'].includes(item.kind)){media.controls=true;media.preload='metadata';media.playsInline=true;}
  else media.alt=`${row.username||'Instagram post'} · media ${libraryState.slide+1}`;
  media.src=`/media/${row.id}/${item.position}`;
  media.addEventListener('error',()=>{$('playback-error').textContent='This local media could not be displayed. Refresh to check the file. A codec unsupported by this browser may still play in a local desktop player.';});
  $('viewer-media').append(media);
}
function slideBy(change){if(!libraryState.viewer)return;const next=libraryState.slide+change;if(next<0||next>=libraryState.viewer.media.length)return;libraryState.slide=next;renderSlide();}
async function postBy(change) {
  const index=libraryState.viewer.index+change;
  if(index<0&&libraryState.page>0){await changeLibraryPage(-1);await openPost(libraryState.rows.length-1,true);}
  else if(index>=libraryState.rows.length&&libraryState.next){await changeLibraryPage(1);await openPost(0,true);}
  else await openPost(index,true);
}
function closePost() {clearPlayback();$('post-viewer').close();window.scrollTo(0,libraryState.scroll);}
$('viewer-close').addEventListener('click',closePost);
$('post-viewer').addEventListener('cancel',()=>{clearPlayback();});
$('post-viewer').addEventListener('close',()=>{clearPlayback();window.scrollTo(0,libraryState.scroll);});
$('slide-previous').addEventListener('click',()=>slideBy(-1));$('slide-next').addEventListener('click',()=>slideBy(1));
$('slide-position').addEventListener('input',()=>{libraryState.slide=Number($('slide-position').value)-1;renderSlide();});
libraryAction('post-previous',()=>postBy(-1));libraryAction('post-next',()=>postBy(1));
$('post-viewer').addEventListener('keydown',event=>{if($('delete-confirm').open||$('details-confirm').open||['INPUT','TEXTAREA','SELECT','VIDEO','AUDIO'].includes(document.activeElement.tagName))return;
  if(event.key==='ArrowLeft'){event.preventDefault();slideBy(-1);}if(event.key==='ArrowRight'){event.preventDefault();slideBy(1);}
  if(event.key==='ArrowUp'){event.preventDefault();postBy(-1).catch(e=>notice(e.message,true));}if(event.key==='ArrowDown'){event.preventDefault();postBy(1).catch(e=>notice(e.message,true));}
});
libraryAction('viewer-favorite',async()=>{const row=libraryState.viewer;await libraryApi('flag',{id:row.id,field:'favorite',value:!row.favorite});row.favorite=!row.favorite;
  $('viewer-favorite').textContent=row.favorite?'★ Favorited':'☆ Favorite';$('viewer-favorite').setAttribute('aria-pressed',String(Boolean(row.favorite)));
  if($('library-favorites').checked&&!row.favorite)closePost();await loadLibrary();
});
libraryAction('viewer-hide',async()=>{const row=libraryState.viewer;await libraryApi('flag',{id:row.id,field:'hidden',value:!row.hidden});closePost();await loadLibrary(true);});
libraryAction('viewer-delete',async()=>{const plan=await libraryApi('delete-preview',{id:libraryState.viewer.id});clearPlayback();libraryState.deletePlan={...plan,id:libraryState.viewer.id};$('delete-error').textContent='';$('delete-files').replaceChildren(...plan.files.map(file=>node('li',file.path+(file.exists?'':' (already missing)'))));$('delete-confirm').showModal();});
$('delete-cancel').addEventListener('click',()=>{$('delete-confirm').close();libraryState.deletePlan=null;renderSlide();});
$('delete-confirm').addEventListener('cancel',()=>{libraryState.deletePlan=null;renderSlide();});
$('viewer-fetch').addEventListener('click',()=>{$('details-error').textContent='';$('details-confirm').showModal();});
$('details-cancel').addEventListener('click',()=>{$('details-confirm').close();});
libraryAction('details-queue',async()=>{const result=await libraryApi('fetch-details',{id:libraryState.viewer.id});$('details-confirm').close();$('viewer-status').textContent=result.message;notice(result.message);});
$('delete-confirmed').addEventListener('click',async()=>{const plan=libraryState.deletePlan;if(!plan)return;$('delete-confirmed').disabled=true;
  try{const result=await libraryApi('delete',{id:plan.id,confirmation:plan.confirmation});$('delete-confirm').close();closePost();notice(result.message);await loadLibrary();}
  catch(e){$('delete-error').textContent=e.message;}finally{$('delete-confirmed').disabled=false;}
});

'use strict';
const $ = selector => document.querySelector(selector);
const messages = $('#messages');
const labels = {chat:'گفت‌وگوی هوشمند',sources:'مرکز اتصال‌ها',audit:'تاریخچهٔ عملیات'};
function switchPanel(panel){
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('visible',p.id===`panel-${panel}`));
  document.querySelectorAll('[data-panel]').forEach(b=>b.classList.toggle('active',b.classList.contains('nav-item')&&b.dataset.panel===panel));
  $('#page-label').textContent=labels[panel];
  if(panel==='audit')refreshAudit();
}
document.querySelectorAll('[data-panel]').forEach(b=>b.addEventListener('click',()=>switchPanel(b.dataset.panel)));
function addMessage(kind,content,response){
  const row=document.createElement('div'); row.className='chat-row '+(kind==='user'?'user-msg':'bot');
  if(kind!=='user'){const avatar=document.createElement('span'); avatar.className='chat-avatar';avatar.textContent='✳';row.append(avatar);}
  const bubble=document.createElement('div');bubble.className='bubble';
  const p=document.createElement('p');p.textContent=content;bubble.append(p);
  if(response){
    for(const source of (response.sources||[])) {const badge=document.createElement('span');badge.className='source-tag';badge.textContent=`منبع: ${source.name} (${source.rows} رکورد)`;bubble.append(badge);bubble.append(document.createElement('br'));}
    if(response.report){const a=document.createElement('a');a.className='report-link';a.href=response.report;a.textContent='↓ دریافت گزارش CSV (قابل بازکردن در Excel)';bubble.append(a);}
    const small=document.createElement('small');small.textContent=response.denied?'درخواست تغییر در دیمو مسدود است.':'Demo only · اطلاعات ساختگی';bubble.append(small);
  }
  row.append(bubble);messages.append(row);messages.scrollTop=messages.scrollHeight;
}
async function ask(text){
  const input=$('#chat-input'), send=$('#send');
  if(!text.trim())return;
  addMessage('user',text);
  send.disabled=true;input.disabled=true;
  try{const res=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});const data=await res.json();if(!res.ok)throw new Error(data.error||'Request failed');addMessage('bot',data.answer,data);}
  catch(error){addMessage('bot','در پردازش درخواست آزمایشی خطا رخ داد: '+error.message);}
  finally{send.disabled=false;input.disabled=false;input.focus();}
}
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();const input=$('#chat-input');const message=input.value;input.value='';ask(message);});
document.querySelectorAll('[data-q]').forEach(button=>button.addEventListener('click',()=>ask(button.dataset.q)));
async function refreshAudit(){
  try {const r=await fetch('/api/audit');const {events}=await r.json();const root=$('#audit-rows');root.replaceChildren();if(!events.length){const empty=document.createElement('div');empty.className='audit-empty';empty.textContent='هنوز درخواستی ثبت نشده است.';root.append(empty);return;}
    for(const e of events){const row=document.createElement('div');row.className='audit-row';for(const field of ['event','capability','result']){const span=document.createElement('span');span.textContent=e[field];if(field==='result')span.className=e.result==='DENIED'?'denied':'ok';row.append(span);}root.append(row);}
  }catch{ $('#audit-rows').textContent='دریافت تاریخچه ممکن نشد.';}
}
fetch('/api/sources').then(r=>r.json()).then(data=>{$('#drive-count').textContent=`${data.documents.length} فایل نمونه`;$('#erp-count').textContent=`${data.projects} پروژه نمونه`;}).catch(()=>{});

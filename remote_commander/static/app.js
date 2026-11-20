'use strict';
const el = id => document.getElementById(id);
const app = {token: sessionStorage.getItem('rcToken') || '', status: null, folder: '.', current: null, history: [], busy: false};
function showToast(message) { const node=el('toast'); node.textContent=message; node.style.display='block'; clearTimeout(showToast.t); showToast.t=setTimeout(()=>node.style.display='none',4000); }
async function api(route, options={}) {
  const request={method:options.method||'GET',headers:{'Authorization':'Bearer '+app.token}};
  if(options.body!==undefined){request.headers['Content-Type']='application/json'; request.body=JSON.stringify(options.body);}
  let response;
  try {response=await fetch(route,request);} catch {throw new Error('Falha na conexão com o agente.');}
  let result;
  try {result=await response.json();} catch {throw new Error('Resposta inesperada do servidor.');}
  if(!response.ok)throw new Error(result.error||'Erro na requisição.');
  return result;
}
function setText(id,value){el(id).textContent=String(value??'');}
function setFlag(id,enabled,label){const node=el(id);node.textContent=label + (enabled?' ✓':' · off');node.classList.toggle('on',enabled);}
async function authenticate() {
  app.token=el('token').value.trim();
  if(!app.token){setText('loginError','Informe o token.');return;}
  el('loginBtn').disabled=true;
  try{
    app.status=await api('/api/status');
    sessionStorage.setItem('rcToken',app.token);
    el('loginPanel').hidden=true;el('app').hidden=false;
    setText('workspace',app.status.workspace);
    setFlag('chipRead',true,'Leitura');setFlag('chipWrite',app.status.write_enabled,'Escrita');
    setFlag('chipExec',app.status.execution_enabled,'Execução');setFlag('chipAI',app.status.ai_enabled,'IA local');
    setText('terminalBadge',app.status.execution_enabled?'Habilitado':'Desativado');
    setText('modelName',app.status.ai_model || 'Não configurado');
    el('runCommand').disabled=!app.status.execution_enabled;
    el('command').disabled=!app.status.execution_enabled;
    el('sendChat').disabled=!app.status.ai_enabled;
    el('prompt').disabled=!app.status.ai_enabled;
    await loadFolder('.');
  }catch(err){setText('loginError',err.message);app.token='';sessionStorage.removeItem('rcToken');}
  finally{el('loginBtn').disabled=false;}
}
function switchTab(name){document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.pane').forEach(x=>x.hidden=x.id!==name);}
function bytesHuman(n){if(n===null || n===undefined)return '';return n<1024?`${n} B`:`${(n/1024).toFixed(1)} KiB`;}
async function loadFolder(path){
  try{const result=await api('/api/files?path='+encodeURIComponent(path));app.folder=path;
    setText('folderPath',path);el('editorArea').hidden=true;
    const parent=el('filesList');parent.replaceChildren();
    if(!result.items.length){const row=document.createElement('div');row.className='empty-row';row.textContent='Nenhum arquivo visível nesta pasta.';parent.append(row);}
    for(const item of result.items){
      const btn=document.createElement('button');btn.className='entry';
      const ico=document.createElement('span');ico.className='ico';ico.textContent=item.type==='directory'?'▣':'▤';
      const name=document.createElement('span');name.className='name';name.textContent=item.name;
      const info=document.createElement('span');info.className='details';info.textContent=item.type==='directory'?'PASTA':bytesHuman(item.bytes);
      btn.append(ico,name,info);btn.addEventListener('click',()=>item.type==='directory'?loadFolder(item.path):openFile(item.path));parent.append(btn);
    }
    if(result.truncated)showToast('Lista limitada aos primeiros 300 itens.');
  }catch(err){showToast(err.message);}
}
async function openFile(path){
  try{const result=await api('/api/file?path='+encodeURIComponent(path));app.current=path;
    setText('fileName',path);setText('fileSize',bytesHuman(result.bytes));
    el('editor').value=result.content;el('editor').readOnly=!app.status.write_enabled;
    el('saveFile').disabled=!app.status.write_enabled;
    el('editorArea').hidden=false;el('editorArea').scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(err){showToast(err.message);}
}
async function saveFile(){
  if(!app.current || !confirm('Salvar alterações no arquivo '+app.current+'?'))return;
  try{await api('/api/file',{method:'POST',body:{path:app.current,content:el('editor').value}});showToast('Arquivo salvo com sucesso.');}
  catch(err){showToast(err.message);}
}
async function runCommand(){
  const command=el('command').value.trim();if(!command)return;
  if(!confirm('Executar este comando no seu computador?\n\n'+command))return;
  el('runCommand').disabled=true;setText('commandOutput','$ '+command+'\n\nExecutando…');
  try{const r=await api('/api/command',{method:'POST',body:{command,cwd:el('cwd').value.trim()||'.'}});
    setText('commandOutput','$ '+command+'\n\n'+r.stdout+(r.stderr?'\n[stderr]\n'+r.stderr:'')+'\n\n[exit '+r.exit_code+']'+(r.truncated?' (saída truncada)':''));}
  catch(err){setText('commandOutput','Erro: '+err.message);}finally{el('runCommand').disabled=false;}
}
function bubble(role,content){
  const holder=el('conversation');const empty=holder.querySelector('.empty');if(empty)empty.remove();
  const msg=document.createElement('div');msg.className='message '+role;
  const who=document.createElement('span');who.className='who';who.textContent=role==='user'?'VOCÊ':'REMOTE COMMANDER';
  const text=document.createElement('span');text.textContent=content;msg.append(who,text);holder.append(msg);holder.scrollTop=holder.scrollHeight;
}
function showPending(action){
  const target=el('pendingArea');target.replaceChildren();
  const wrap=document.createElement('div');wrap.className='approval';
  const head=document.createElement('h3');head.textContent='⚠ Aprovação necessária: '+action.name;
  const details=document.createElement('pre');details.textContent=action.name==='run_command'?`Diretório: ${action.cwd}\nComando: ${action.command}`:`Arquivo: ${action.path}\nNovo conteúdo (prévia):\n${action.content_preview}`;
  const actions=document.createElement('div');actions.className='actions';
  const cancel=document.createElement('button');cancel.className='small';cancel.textContent='Rejeitar';
  const approve=document.createElement('button');approve.className='primary danger';approve.textContent='Autorizar ação';
  async function decide(value){approve.disabled=true;cancel.disabled=true;
    try{const result=await api('/api/approve',{method:'POST',body:{id:action.id,approved:value}});
      const display=result.approved?'Resultado: '+JSON.stringify(result.result,null,2):'Ação rejeitada.';
      bubble('assistant',display);app.history.push({role:'assistant',content:display});target.replaceChildren();
    }catch(err){showToast(err.message);approve.disabled=false;cancel.disabled=false;}
  }
  cancel.addEventListener('click',()=>decide(false));approve.addEventListener('click',()=>decide(true));
  actions.append(cancel,approve);wrap.append(head,details,actions);target.append(wrap);wrap.scrollIntoView({behavior:'smooth',block:'nearest'});
}
async function sendChat(){
  const prompt=el('prompt').value.trim();if(!prompt || app.busy)return;
  const previous=app.history.slice(-10);
  bubble('user',prompt);app.history.push({role:'user',content:prompt});el('prompt').value='';app.busy=true;el('sendChat').disabled=true;
  try{const result=await api('/api/chat',{method:'POST',body:{prompt,history:previous}});
    bubble('assistant',result.reply||'(Sem texto de resposta.)');app.history.push({role:'assistant',content:result.reply||''});
    if(result.pending)showPending(result.pending);
  }catch(err){bubble('assistant','Erro: '+err.message);}
  finally{app.busy=false;el('sendChat').disabled=false;}
}
el('loginBtn').addEventListener('click',authenticate);
el('token').addEventListener('keydown',event=>{if(event.key==='Enter')authenticate();});
el('logout').addEventListener('click',()=>{sessionStorage.removeItem('rcToken');location.reload();});
document.querySelectorAll('.tabs button').forEach(btn=>btn.addEventListener('click',()=>switchTab(btn.dataset.tab)));
el('refreshFiles').addEventListener('click',()=>loadFolder(app.folder));
el('goUp').addEventListener('click',()=>{const bits=app.folder.split('/').filter(x=>x!=='.'&&x!=='');bits.pop();loadFolder(bits.join('/')||'.');});
el('closeFile').addEventListener('click',()=>el('editorArea').hidden=true);
el('saveFile').addEventListener('click',saveFile);
el('runCommand').addEventListener('click',runCommand);
el('command').addEventListener('keydown',event=>{if(event.key==='Enter')runCommand();});
el('sendChat').addEventListener('click',sendChat);
el('prompt').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat();}});
if(app.token){el('token').value=app.token;authenticate();}

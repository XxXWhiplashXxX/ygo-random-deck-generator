let current=null, defaults={};
const form=document.getElementById('settings');
const $=id=>document.getElementById(id);
const settings=()=>Object.fromEntries([...form.elements].filter(el=>el.name).map(el=>[el.name,el.type==='checkbox'?el.checked:el.type==='range'?Number(el.value):el.value]));
const put=(k,v)=>{const el=form.elements.namedItem(k);if(el){if(el.type==='checkbox')el.checked=!!v;else el.value=v??'';}};
const get=k=>form.elements.namedItem(k);
const allocation=['deck_type_overall_pct','support_overall_pct','generic_overall_pct'];
const mix=['monster_target','spell_target','trap_target'];
const clamp=(n,min,max)=>Math.max(min,Math.min(max,Math.round(Number(n)||0)));
function balance(keys,changed,total){
  const val=clamp(get(changed).value,0,total);
  get(changed).value=val;
  const others=keys.filter(k=>k!==changed);
  let remaining=total-val;
  const weights=others.map(k=>Math.max(0,Number(get(k).value)||0));
  const sum=weights.reduce((a,b)=>a+b,0);
  const raw=others.map((_,i)=>sum?remaining*weights[i]/sum:remaining/others.length);
  const amounts=raw.map(Math.floor);
  let left=remaining-amounts.reduce((a,b)=>a+b,0);
  const order=raw.map((v,i)=>({i,f:v-amounts[i]})).sort((a,b)=>b.f-a.f);
  for(let i=0;i<left;i++)amounts[order[i].i]++;
  others.forEach((k,i)=>get(k).value=amounts[i]);
}
function sync(){
  for(const field of document.querySelectorAll('.percent-number'))field.value=get(field.dataset.for).value;
  const sum=allocation.reduce((n,k)=>n+Number(get(k).value),0);
  $('total').textContent=sum+'%';
  $('total').style.color=sum===100?'#a8c6ff':'#ff9b9b';
}
// Search category VALUE (the text after '='), but always display the complete label.
let labelOptions=[];
function setupLabelSearch(labels){labelOptions=labels;
  const searchValue=label=>label.includes('=')?label.split(/=(.*)/s)[1].trim().toLocaleLowerCase():label.trim().toLocaleLowerCase();
  for(const input of document.querySelectorAll('input.label-search')){
    const popup=input.nextElementSibling;
    let selected='',active=-1,visible=[];
    input.setAttribute('aria-controls',`suggestions-${input.name}`);
    popup.id=`suggestions-${input.name}`;
    function close(){popup.hidden=true;input.setAttribute('aria-expanded','false');active=-1;}
    function choose(value){selected=value;input.dataset.lastValid=value;input.value=value;close();input.dispatchEvent(new Event('change',{bubbles:true}));}
    function updateActive(){[...popup.children].forEach((node,i)=>{node.classList.toggle('active',i===active);node.setAttribute('aria-selected',String(i===active));});}
    function show(){
      const typed=input.value.trim().toLocaleLowerCase();
      // Don't filter by the non-searchable prefix of a fully selected label.
      const query=labels.includes(input.value)?'':typed;
      visible=(query?labels.filter(label=>searchValue(label).includes(query)):labels).slice(0,40);
      popup.replaceChildren();
      visible.forEach((label,i)=>{
        const item=document.createElement('div');item.className='label-option';item.role='option';item.id=`${popup.id}-${i}`;item.textContent=label;
        item.addEventListener('mousedown',e=>{e.preventDefault();choose(label);});
        popup.appendChild(item);
      });
      active=-1;popup.hidden=!visible.length;
      input.setAttribute('aria-expanded',String(!!visible.length));
    }
    input.addEventListener('focus',show);
    input.addEventListener('input',()=>{if(labels.includes(input.value)){selected=input.value;input.dataset.lastValid=selected;}show();});
    input.addEventListener('keydown',e=>{
      if(e.key==='Escape'){close();return;}
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){
        e.preventDefault();if(popup.hidden)show();
        if(!visible.length)return;
        active=(active+(e.key==='ArrowDown'?1:-1)+visible.length)%visible.length;
        updateActive();popup.children[active].scrollIntoView({block:'nearest'});
      }else if(e.key==='Enter'&&!popup.hidden){
        if(visible.length){e.preventDefault();choose(visible[Math.max(0,active)]);}
      }
    });
    input.addEventListener('blur',()=>{setTimeout(()=>{if(!labels.includes(input.value))input.value=selected||input.dataset.lastValid||labels[0]||'';close();},100);});
    // `put()` updates .value directly, so restore the last valid value on focus.
    input.addEventListener('focus',()=>{if(labels.includes(input.value)){selected=input.value;input.dataset.lastValid=selected;}});
  }
}
async function init(){const res=await fetch('/api/options');if(!res.ok)throw Error('Could not load settings');const data=await res.json();defaults=data.defaults;for(const select of document.querySelectorAll('select[data-options]')){const values=data[select.dataset.options];select.replaceChildren(...values.map(v=>new Option(v,v)));}setupLabelSearch(data.labels);for(const [k,v] of Object.entries(defaults))put(k,v);
  const desired={main_size:60,monster_target:30,spell_target:20,trap_target:10,
    min_rating:3,max_rating:5,deck_type_overall_pct:50,
    support_overall_pct:30,generic_overall_pct:20,support_on:true,allow_normals:false};
  for(const [k,v] of Object.entries(desired))put(k,v);
  sync();}
form.addEventListener('input',e=>{
  const target=e.target;
  const key=target.dataset.for||target.name;
  if(target.matches('.percent-number')){
    if(target.value==='')return;
    put(key,clamp(target.value,0,100));
  }
  if(allocation.includes(key))balance(allocation,key,100);
  if(mix.includes(key))balance(mix,key,clamp(get('main_size').value,40,60));
  if(key==='main_size'){
    const size=clamp(target.value,40,60);
    const old=mix.reduce((n,k)=>n+Number(get(k).value),0);
    const current=mix.map(k=>Number(get(k).value));
    const scaled=current.map(v=>old?Math.floor(v*size/old):0);
    let remaining=size-scaled.reduce((a,b)=>a+b,0);
    for(let i=0;i<remaining;i++)scaled[i%3]++;
    mix.forEach((k,i)=>put(k,scaled[i]));
  }
  sync();
});
form.addEventListener('change',e=>{
  if(e.target.matches('.percent-number')&&e.target.value==='')e.target.value=get(e.target.dataset.for).value;
});
function safe(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function grid(id,cards){$(id).replaceChildren(...cards.map(c=>{const div=document.createElement('div');div.className='card';const img=document.createElement('img');img.loading='lazy';img.alt=c.name;img.src='/api/card-image/'+encodeURIComponent(c.id);img.onerror=()=>{img.style.visibility='hidden';};const name=document.createElement('strong');name.textContent=c.name;name.title=c.reason;const origin=document.createElement('span');origin.textContent=c.origin;div.append(img,name,origin);return div;}));}
function headerText(){if(!current)return '';const c=current.categories;return `Yu-Gi-Oh! Random Deck Generator\nSeed: ${current.seed}\n${current.breakdown}\nSelected categories:\n${[['theme1','Theme 1'],['theme2','Theme 2'],['support1','Support 1'],['support2','Support 2']].map(([k,n])=>`  ${n}: ${c[k]?.label||'Automatic'}${c[k]?.detail?' — '+c[k].detail:''}`).join('\n')}\n`;}
function deckText(){return headerText()+'\n'+[['Main Deck',current.main],['Extra Deck',current.extra]].map(([name,cards])=>`${name} (${cards.length}):\n`+cards.map(c=>`- ${c.name} [${c.origin}]${c.reason?' :: '+c.reason:''}`).join('\n')).join('\n\n')+'\n';}
function show(d){current=d;const c=d.categories;$('resultTitle').textContent='Generated deck · seed '+d.seed;$('status').textContent='Click a card name to see why it was selected. Export or copy your result below.';$('summary').classList.remove('hidden');$('summary').innerHTML='<strong>Selected categories</strong><br>'+[['theme1','Theme 1'],['theme2','Theme 2'],['support1','Support 1'],['support2','Support 2']].map(([k,n])=>`${n}: ${safe(c[k]?.label||'Automatic')}${c[k]?.detail?' — '+safe(c[k].detail):''}`).join('<br>')+'<hr><pre>'+safe(d.breakdown)+'</pre>';$('deck').classList.remove('hidden');$('mainCount').textContent=d.main.length+' cards';$('extraCount').textContent=d.extra.length+' cards';grid('mainCards',d.main);grid('extraCards',d.extra);$('log').textContent=deckText();for(const id of ['png','ydk','txt','copy'])$(id).disabled=false;for(const [k,v] of Object.entries(d.settings))put(k,v);sync();}
form.addEventListener('submit',async e=>{e.preventDefault();for(const input of document.querySelectorAll('input.label-search')){if(document.activeElement===input)input.blur();if(!labelOptions.includes(input.value))input.value=input.dataset.lastValid||labelOptions[0]||'';}const v=settings();if(['deck_type_overall_pct','support_overall_pct','generic_overall_pct'].reduce((n,k)=>n+v[k],0)!==100){$('status').textContent='Theme + Support + Generic must equal 100%.';return;}const b=$('generate');b.disabled=true;b.textContent='Generating…';$('status').textContent='Generating deck. This may take a moment.';try{const res=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:v})});const d=await res.json();if(!res.ok)throw Error(d.detail||'Generation failed');show(d);}catch(err){$('status').textContent=err.message;}finally{b.disabled=false;b.textContent='Generate Deck';}});
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}
$('txt').onclick=()=>download(new Blob([deckText()],{type:'text/plain;charset=utf-8'}),`ygo-deck-${current.seed}.txt`);
$('ydk').onclick=()=>{const ids=arr=>arr.map(c=>c.id).filter(x=>/^\d+$/.test(x)).join('\n');download(new Blob([`#created by ygo_randomizer\n#main\n${ids(current.main)}\n#extra\n${ids(current.extra)}\n!side\n`],{type:'text/plain'}),`ygo-deck-${current.seed}.ydk`);};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(deckText());$('status').textContent='Decklist copied.';}catch(e){$('status').textContent='Clipboard unavailable; use Export TXT.';}};
$('png').onclick=async()=>{const button=$('png');button.disabled=true;button.textContent='Rendering…';try{const width=1500,margin=35,cols=10,cardW=124,cardH=181,gap=13,rowH=cardH+37,rows=n=>Math.ceil(n/cols),titleH=200,mainY=titleH+50,extraY=mainY+rows(current.main.length)*rowH+80,height=extraY+rows(current.extra.length)*rowH+75;const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const ctx=canvas.getContext('2d');ctx.fillStyle='#111827';ctx.fillRect(0,0,width,height);ctx.fillStyle='#f3f6ff';ctx.font='bold 35px Arial';ctx.fillText('YU-GI-OH! RANDOM DECK',margin,53);ctx.font='20px Arial';ctx.fillStyle='#aac2e9';ctx.fillText('Seed: '+current.seed,margin,89);ctx.font='17px Arial';const pairs=[['theme1','Theme 1'],['theme2','Theme 2'],['support1','Support 1'],['support2','Support 2']];pairs.forEach(([k,label],i)=>{let str=label+': '+(current.categories[k]?.label||'Automatic');ctx.fillText(str.slice(0,85),margin,120+i*23);});const imageCache=new Map();async function load(id){if(imageCache.has(id))return imageCache.get(id);const promise=new Promise(resolve=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>resolve(null);img.src='/api/card-image/'+encodeURIComponent(id);});imageCache.set(id,promise);return promise;}
async function section(cards,y,title){ctx.fillStyle='#f2f5ff';ctx.font='bold 27px Arial';ctx.fillText(`${title} (${cards.length})`,margin,y-17);await Promise.all(cards.map(async(c,i)=>{const x=margin+(i%cols)*(cardW+gap),yy=y+Math.floor(i/cols)*rowH;const img=await load(c.id);if(img)ctx.drawImage(img,x,yy,cardW,cardH);else{ctx.fillStyle='#34445c';ctx.fillRect(x,yy,cardW,cardH);}ctx.fillStyle='#dbe6fb';ctx.font='12px Arial';let name=c.name;if(name.length>19)name=name.slice(0,18)+'…';ctx.fillText(name,x,yy+cardH+17);}));}
await section(current.main,mainY,'MAIN DECK');await section(current.extra,extraY,'EXTRA DECK');const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('PNG export failed');download(blob,`ygo-deck-${current.seed}.png`);}catch(e){$('status').textContent='PNG export failed: '+e.message;}finally{button.disabled=false;button.textContent='Export PNG';}};
init().catch(e=>$('status').textContent=e.message);

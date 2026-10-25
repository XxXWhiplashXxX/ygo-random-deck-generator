// This script and these endpoints exist only in the offline launcher.
(()=>{
  const $=id=>document.getElementById(id);
  let polling=null;
  let previousPhase='idle';
  const status=async()=>{
    const res=await fetch('/api/local-update/status');
    if(!res.ok)throw Error('Unable to read updater status');
    const s=await res.json();
    $('updateStatus').textContent=s.error||({checking:'Checking for updates…',updating:'Updating local files…',done:'Ready.',idle:'Not checked yet.'}[s.phase]);
    $('updateLog').textContent=s.logs.join('\n');
    $('updateCheck').disabled=s.busy;
    $('updateCards').disabled=s.busy;
    $('updateLists').disabled=s.busy;
    if(s.check){
      $('updateChoices').classList.remove('hidden');
      $('updateCards').hidden=!s.check.needs_cards;
      $('updateLists').hidden=false;
      if(s.phase==='done' && previousPhase==='checking'){
        $('updateStatus').textContent=s.check.needs_cards
          ? `Update available: latest ${s.check.latest}, installed ${s.check.installed||'unknown'}. Choose Update Card Database to rebuild.`
          : `Cards up to date (${s.check.latest}). You can still refresh forbidden lists.`;
      }
    }
    if(s.busy){if(!polling)polling=setInterval(status,1200);}
    else if(polling){clearInterval(polling);polling=null;}
    previousPhase=s.phase;
  };
  const action=async(path)=>{
    try{
      const res=await fetch('/api/local-update/'+path,{method:'POST'});
      if(!res.ok){const d=await res.json();throw Error(d.detail||'Update request failed');}
      await status();
    }catch(e){$('updateStatus').textContent=e.message;}
  };
  $('updateCheck').onclick=()=>action('check');
  $('updateCards').onclick=()=>{
    if(confirm('Rebuild cards.csv and Stats.csv now? This may take several minutes and requires internet access.'))action('run/cards');
  };
  $('updateLists').onclick=()=>action('run/banlists');
  status().catch(e=>$('updateStatus').textContent=e.message);
})();

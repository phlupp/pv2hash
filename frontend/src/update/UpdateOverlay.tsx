import {useEffect,useRef,useState} from 'react';
import {
  Alert,Badge,Button,Code,Collapse,Group,Modal,Progress,ScrollArea,Stack,Text,TextInput,
} from '@mantine/core';
import {IconCheck,IconChevronDown,IconChevronRight,IconCopy,IconDownload,IconTerminal2} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';

export type UpdateRunner={
  status?:string;message?:string;running?:boolean;can_start?:boolean;
  progress_percent?:number|null;phase_step?:number;phase_total?:number;
  phase_name?:string;phase_text?:string;
  last_error?:string;target_version_full?:string;target_tag?:string;
  started_at?:string|null;finished_at?:string|null;
};
type LogChunk={status:string;available:boolean;lines:string[];offset:number;file_id:string;reset:boolean;truncated:boolean};

export const INSTALL_PHASES=[
  'Systempakete prüfen','Release-Metadaten laden',
  'Herunterladen und Prüfsummen kontrollieren',
  'Release-Dateien entpacken',
  'Python-Umgebung und Abhängigkeiten einrichten',
  'Neue Version aktivieren','PV2Hash-Dienst neu starten',
] as const;

function severity(line:string):string {
  if(/\b(error|failed|fehler|traceback)\b/i.test(line))return '#ff8787';
  if(/\b(warn|warning|achtung)\b/i.test(line))return '#ffd43b';
  if(/\b(ok|erfolgreich|success)\b/i.test(line))return '#63e6be';
  return '#cbd5e1';
}

export function UpdateOverlay({
  opened,onClose,runner,connecting=false,preview=false,autoDashboard=false,
}:{
  opened:boolean;onClose:()=>void;runner?:UpdateRunner;
  connecting?:boolean;preview?:boolean;autoDashboard?:boolean;
}){
  const [visible,setVisible]=useState(true);
  const [autoScroll,setAutoScroll]=useState(true);
  const [filter,setFilter]=useState('');
  const [lines,setLines]=useState<string[]>([]);
  const [truncated,setTruncated]=useState(false);
  const [available,setAvailable]=useState(true);
  const [logError,setLogError]=useState('');
  const [demoStep,setDemoStep]=useState(3);
  const cursor=useRef({offset:0,file_id:''});
  const scroll=useRef<HTMLDivElement>(null);
  const pending=useRef(false);
  const status=preview?(demoStep>7?'success':'running'):(runner?.status||'idle');
  const running=status==='running'||status==='starting';
  const phaseStep=preview?Math.min(demoStep,7):runner?.phase_step;
  const phaseTotal=preview?7:runner?.phase_total;
  const phaseName=preview?INSTALL_PHASES[Math.min(demoStep-1,6)]:runner?.phase_name;
  const progress=preview?(demoStep>7?100:Math.round((demoStep-1)/7*100))
    :runner?.progress_percent;
  const mockLines=[
    '[SIMULATION] Update auf r2026.10.18.1 gestartet.',
    ...INSTALL_PHASES.slice(0,Math.min(7,demoStep)).flatMap((phase,i)=>[
      `[${String(i+1).padStart(2,'0')}/07] ${phase}`,
      ...(i<demoStep-1?['OK: Schritt abgeschlossen.']:['Installationsvorgang läuft …']),
    ]),
    ...(demoStep>7?['SUCCESS: Installation und Versionsprüfung erfolgreich.']:[]),
  ];
  useEffect(()=>{
    if(!opened||preview)return;
    let active=true;
    cursor.current={offset:0,file_id:''};
    setLines([]);setLogError('');setTruncated(false);
    async function poll(){
      if(pending.current)return;
      pending.current=true;
      try{
        const p=new URLSearchParams({offset:String(cursor.current.offset),file_id:cursor.current.file_id});
        const r=await fetch('/api/system/update-log?'+p.toString(),{cache:'no-store'});
        if(!r.ok)throw new Error('HTTP '+r.status);
        const x=await r.json() as LogChunk;
        if(!active)return;
        setAvailable(x.available);setLogError('');
        cursor.current={offset:x.offset,file_id:x.file_id};
        setTruncated(x.truncated);
        setLines(old=>(x.reset?x.lines:[...old,...x.lines]).slice(-1500));
      }catch(e){
        if(active)setLogError(e instanceof Error?e.message:'Verbindung unterbrochen');
      }finally{pending.current=false;}
    }
    void poll();
    const id=setInterval(()=>void poll(),1500);
    return ()=>{active=false;clearInterval(id);};
  },[opened,preview]);
  const shown=(preview?mockLines:lines).filter(line=>line.toLowerCase().includes(filter.toLowerCase()));
  useEffect(()=>{if(autoScroll&&scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight;},[autoScroll,shown.length,visible]);
  const phaseLine=phaseStep&&phaseTotal
    ?`Schritt ${phaseStep} von ${phaseTotal}: ${phaseName||'Installation läuft'}`
    :runner?.message||'Warte auf Installationsstatus …';
  const canClose=!running||preview;
  return <Modal opened={opened} onClose={canClose?onClose:()=>{}}
    closeOnEscape={canClose} closeOnClickOutside={false}
    withCloseButton={canClose} centered size="lg" title={
      <Group gap="xs">
        <IconTerminal2 size={18}/>
        <Text fw={650}>PV2Hash Update-Manager</Text>
        {preview&&<Badge size="xs" color="orange">SIMULATION</Badge>}
      </Group>
    }>
    <Stack gap="sm">
      <Group justify="space-between" gap="xs">
        <Text fw={600} size="sm">{status==='success'?'Update erfolgreich':status==='error'?'Update fehlgeschlagen':running&&connecting?'Verbindung wird wiederhergestellt …':running?'Installation läuft':'Kein Update aktiv'}</Text>
        <Badge color={status==='error'?'red':status==='success'?'teal':'gray'} size="sm">
          {preview?'Testlauf':runner?.target_tag||runner?.target_version_full||'Update'}
        </Badge>
      </Group>
      <Text c="dimmed" size="xs">{phaseLine}</Text>
      {(running||status==='success'||status==='error')&&<>
        {typeof progress==='number'?<Progress value={Math.max(0,Math.min(progress,100))} size="sm" radius="md"/>
          :running?<Progress value={100} animated striped size="sm" color="gray"/>
            :<Progress value={0} size="sm" color="red"/>}
        <Group justify="space-between">
          <Text size="xs" c="dimmed">{typeof progress==='number'?`${progress}% abgeschlossene Arbeitsschritte`
            :running?'Fortschritt wird ermittelt':'Kein abgeschlossener Fortschritt'}</Text>
          <Text size="xs" c="dimmed">{phaseStep&&phaseTotal?`${phaseStep}/${phaseTotal}`:'—'}</Text>
        </Group>
      </>}
      {status==='error'&&<Alert color="red">{runner?.last_error||'Weitere Einzelheiten stehen im Protokoll.'}</Alert>}
      {!preview&&status==='success'&&autoDashboard&&<Alert color="teal">
        Neue Version bestätigt. Das Dashboard wird gleich automatisch neu geladen.
      </Alert>}
      {connecting&&running&&<Alert color="gray">Der PV2Hash-Dienst startet möglicherweise neu. Die Anzeige wartet auf die Wiederverbindung.</Alert>}
      <Group justify="space-between" gap="xs">
        <Button size="compact-sm" variant="subtle" color="gray"
          rightSection={visible?<IconChevronDown size={14}/>:<IconChevronRight size={14}/>}
          onClick={()=>setVisible(x=>!x)}>Installationsprotokoll {visible?'ausblenden':'anzeigen'}</Button>
        {!preview&&<Button component="a" href="/api/system/update-log/download"
          variant="subtle" color="gray" size="compact-sm" leftSection={<IconDownload size={14}/>}>Log herunterladen</Button>}
      </Group>
      <Collapse expanded={visible}>
        <Stack gap="xs">
          <Group gap="xs">
            <TextInput size="xs" placeholder="Konsole durchsuchen …" aria-label="Update-Log filtern"
              style={{flex:1}} value={filter} onChange={e=>setFilter(e.currentTarget.value)}/>
            <Button size="compact-sm" color="gray" variant="subtle"
              onClick={()=>setAutoScroll(v=>!v)}>{autoScroll?'Auto-Scroll an':'Auto-Scroll aus'}</Button>
            <Button size="compact-sm" variant="subtle" color="gray" title="Log kopieren"
              leftSection={<IconCopy size={13}/>} onClick={()=>{
                if(navigator.clipboard)void navigator.clipboard.writeText((preview?mockLines:lines).join('\n'));
              }}>Kopieren</Button>
          </Group>
          <div ref={scroll} role="log" aria-label="Installationskonsole" style={{
            height:260,overflow:'auto',padding:'12px',borderRadius:6,
            background:'#0b1019',border:'1px solid #263244',
            fontFamily:'ui-monospace, SFMono-Regular, Consolas, monospace',
            fontSize:11,lineHeight:1.65,
          }}>
            {truncated&&<Text size="xs" c="yellow">Ältere Logzeilen gekürzt.</Text>}
            {shown.map((line,i)=><div key={i} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',color:severity(line)}}>
              {line}
            </div>)}
            {!shown.length&&<Text c="dimmed" size="xs">{available?'Warte auf Protokolleinträge …':'Noch kein Installationsprotokoll vorhanden.'}</Text>}
          </div>
          {logError&&<Text size="xs" c="orange">Protokoll wird erneut geladen: {logError}</Text>}
        </Stack>
      </Collapse>
      {preview&&<Group justify="space-between" gap="xs">
        <Text size="xs" c="orange">Nur GUI-Simulation. Keine Installation wird ausgeführt.</Text>
        <Button size="sm" variant="light" color="gray" disabled={demoStep>7}
          onClick={()=>setDemoStep(s=>s+1)}>Nächster Schritt</Button>
      </Group>}
      {canClose&&<Group justify="flex-end">
        <Button color="gray" variant="light" size="sm" onClick={onClose}>Schließen</Button>
      </Group>}
    </Stack>
  </Modal>;
}

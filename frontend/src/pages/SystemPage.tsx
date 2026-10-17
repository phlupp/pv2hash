import {useEffect,useRef,useState} from 'react';
import {
  Alert,Badge,Button,Card,Code,Collapse,Divider,FileInput,Group,Modal,
  Select,SimpleGrid,Stack,Text,TextInput,Title,
} from '@mantine/core';
import {IconAlertCircle,IconDownload,IconRefresh,IconUpload,IconSearch} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {configJson} from '../configuration/api';
import {UpdateOverlay,type UpdateRunner} from '../update/UpdateOverlay';
import {ReleaseNotes} from '../update/ReleaseNotes';
import {version as guiVersion} from '../../package.json';

type SystemRow={label:string;value:string|number|null};
type SystemAction={id:string;label:string;style?:string};
type Runner=UpdateRunner;
type ReleaseStatus={
  status:string;local_version_full?:string;release_version_full?:string;checked_at?:string;
  release_id?:string;installed_release_id?:string;
  release_core_version?:string;release_gui_version?:string;
  release_published_at?:string;error?:string;
};
type UpdateModel={
  update_status:ReleaseStatus;
  runner_status:Runner;
  release_details?:{name?:string;body?:string;asset_name?:string;asset_size_text?:string};
};
type SystemCardModel={
  id:string;type:string;title:string;description?:string;
  rows?:SystemRow[];actions?:SystemAction[];
  log_level?:string;allowed_log_levels?:string[];
  download_url?:string;export_url?:string;
  model?:UpdateModel;
};
type SystemModel={instance_name:string;app_version_full:string;cards:SystemCardModel[]};
type ApiResult={status?:string;message?:string;model?:SystemModel};
async function get<T>(path:string):Promise<T>{
  return configJson<T & {status?:string;message?:string}>(fetch(path,{cache:'no-store'}));
}
async function post<T extends {status?:string;message?:string}>(path:string,body?:unknown){
  return configJson<T>(fetch(path,{method:'POST',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}));
}
const failed=(e:unknown)=>notifications.show({color:'red',message:e instanceof Error?e.message:'Aktion fehlgeschlagen.'});
const formatTime=(s?:string)=>s?new Date(s).toLocaleString('de-DE'):'—';

function Rows({rows}:{rows?:SystemRow[]}){
  return <SimpleGrid cols={{base:2,md:3,lg:4}} spacing="sm">
    {(rows||[]).map((row,i)=><div key={row.label+'-'+i} style={{minWidth:0}}>
      <Text size="xs" c="dimmed">{row.label}</Text>
      <Text fw={600} size="sm" style={{overflowWrap:'anywhere'}}>{row.value??'—'}</Text>
    </div>)}
  </SimpleGrid>;
}

function UpdateCard({card,busy,onRefresh,onStart,onLog}:{card:SystemCardModel;busy:boolean;onRefresh:()=>void;onStart:()=>void;onLog:()=>void}){
  const u=card.model?.update_status;
  const runner=card.model?.runner_status;
  const details=card.model?.release_details;
  const [open,setOpen]=useState(false);
  return <Card>
    <Stack gap="sm">
      <Group justify="space-between" gap="xs">
        <Text fw={650}>{card.title}</Text>
        <Group gap="xs">
          <Badge color={u?.status==='update_available'?'orange':u?.status==='error'?'red':'gray'} size="sm" variant="light">
            {u?.status==='update_available'?'Update verfügbar':u?.status==='up_to_date'?'Aktuell':u?.status==='error'?'Prüffehler':u?.status||'Unbekannt'}
          </Badge>
          <Button size="compact-sm" variant="light" color="gray" leftSection={<IconSearch size={14}/>}
            disabled={busy} onClick={onRefresh}>Auf Updates prüfen</Button>
        </Group>
      </Group>
      <Rows rows={[
        {label:'Installierte Core-Version',value:u?.local_version_full||'—'},
        {label:'Installierte GUI',value:guiVersion},
        {label:'Installiertes Paket',value:u?.installed_release_id||'Legacy-Installation'},
        {label:'Verfügbare Release-ID',value:u?.release_id||u?.release_version_full||'—'},
        {label:'Verfügbarer Core',value:u?.release_core_version||u?.release_version_full||'—'},
        {label:'Verfügbare GUI',value:u?.release_gui_version||'Noch keine Paket-Metadaten'},
        {label:'Geprüft am',value:formatTime(u?.checked_at)},
        {label:'Veröffentlicht am',value:formatTime(u?.release_published_at)},
      ]}/>
      {(u?.error||runner?.last_error)&&<Alert color="red">{u?.error||runner?.last_error}</Alert>}
      {runner?.message&&<Text c="dimmed" size="xs">{runner.message}</Text>}
      {!!(details?.name||details?.body)&&<div>
        <Button variant="subtle" size="compact-sm" color="gray" onClick={()=>setOpen(x=>!x)}>
          {open?'Release-Details schließen':'Release-Details anzeigen'}
        </Button>
        <Collapse expanded={open}>
          <Text fw={600} size="sm">{details.name||'Release'}</Text>
          <Text size="xs" c="dimmed">{details.asset_name||'—'} · {details.asset_size_text||'—'}</Text>
          <ReleaseNotes markdown={details.body||''}/>
        </Collapse>
      </div>}
      <div>
        <Text size="xs" c="dimmed" mb={4}>Manueller Installationsbefehl für die aktuelle Stable-Version</Text>
        <Code block style={{overflowWrap:'anywhere',whiteSpace:'pre-wrap'}}>curl -fsSL https://github.com/phlupp/pv2hash/releases/latest/download/install.sh | sudo bash</Code>
      </div>
      <Group justify="flex-end">
        <Button size="sm" color="gray" variant="subtle" onClick={onLog}>
          Installationsprotokoll
        </Button>
        <Button size="sm" color="orange" variant="light"
          disabled={busy||!runner?.can_start||u?.status!=='update_available'||runner?.running}
          onClick={onStart}>{runner?.running?'Update läuft …':'Update starten'}</Button>
      </Group>
    </Stack>
  </Card>;
}

function LiveLogs({card,busy,onLogLevel}:{card:SystemCardModel;busy:boolean;onLogLevel:(level:string)=>Promise<void>}){
  const [level,setLevel]=useState(card.log_level||'INFO');
  const [filter,setFilter]=useState('');
  const [show,setShow]=useState(true);
  const [auto,setAuto]=useState(true);
  const scrollRef=useRef<HTMLDivElement>(null);
  const logs=useQuery({queryKey:['system','logs'],queryFn:()=>get<{lines:string[]}>('/api/logs'),
    enabled:show,refetchInterval:show?5000:false,refetchIntervalInBackground:false});
  const lines=(logs.data?.lines||[]).filter(line=>line.toLowerCase().includes(filter.toLowerCase()));
  useEffect(()=>{if(auto&&scrollRef.current)scrollRef.current.scrollTop=scrollRef.current.scrollHeight;},[auto,lines.length]);
  useEffect(()=>setLevel(card.log_level||'INFO'),[card.log_level]);
  return <Card>
    <Stack gap="sm">
      <Group justify="space-between"><Text fw={650}>{card.title}</Text>
        <Badge size="xs" color={logs.error?'red':'gray'} variant="light">{logs.error?'Logfehler':show?'Live':'Pausiert'}</Badge>
      </Group>
      <Text size="xs" c="dimmed">{card.description}</Text>
      <Group align="end" gap="xs" wrap="wrap">
        <Select label="Log-Level" size="xs" value={level} allowDeselect={false}
          data={card.allowed_log_levels||['INFO','DEBUG']}
          onChange={v=>setLevel(v||'INFO')} w={135}/>
        <Button size="compact-sm" color="gray" variant="light" disabled={busy} onClick={()=>void onLogLevel(level)}>Log-Level setzen</Button>
        <Button component="a" href={card.download_url||'/api/logs/download'} variant="subtle" color="gray"
          size="compact-sm" leftSection={<IconDownload size={14}/>}>Log herunterladen</Button>
      </Group>
      <Group gap="xs">
        <TextInput size="xs" placeholder="Logzeilen filtern …" aria-label="Logs filtern"
          value={filter} onChange={e=>setFilter(e.currentTarget.value)}
          style={{flex:1,minWidth:150}}/>
        <Button size="compact-sm" variant="subtle" color="gray" onClick={()=>setFilter('')}>Filter löschen</Button>
        <Button size="compact-sm" variant="subtle" color="gray" onClick={()=>setAuto(a=>!a)}>
          {auto?'Autoscroll an':'Autoscroll aus'}</Button>
        <Button size="compact-sm" variant="subtle" color="gray" onClick={()=>setShow(a=>!a)}>
          {show?'Ausblenden':'Einblenden'}</Button>
      </Group>
      {logs.error&&<Text c="red" size="xs">{logs.error.message}</Text>}
      {show&&<div ref={scrollRef} style={{
        maxHeight:325,minHeight:190,overflowY:'auto',overflowX:'auto',padding:10,borderRadius:4,
        border:'1px solid var(--mantine-color-dark-5)',background:'var(--mantine-color-dark-9)',
        fontFamily:'ui-monospace,monospace',fontSize:11,lineHeight:1.65,
      }}>
        {lines.length?lines.map((line,i)=><div key={i} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{line}</div>)
          :<Text size="xs" c="dimmed">Keine Logzeilen vorhanden.</Text>}
      </div>}
    </Stack>
  </Card>;
}

export function SystemPage(){
  const client=useQueryClient();
  const system=useQuery({queryKey:['system','model'],queryFn:()=>get<SystemModel>('/api/system/model'),refetchInterval:5000,refetchIntervalInBackground:false});
  const [busy,setBusy]=useState(false);
  const [pending,setPending]=useState<'update'|'import'|'reload'|null>(null);
  const [file,setFile]=useState<File|null>(null);
  const [updateOverlay,setUpdateOverlay]=useState(false);
  const [previewOverlay,setPreviewOverlay]=useState(false);
  const updater=useQuery({queryKey:['system','runner'],queryFn:()=>get<Runner>('/api/system/self-update-status'),
    refetchInterval:updateOverlay?1500:5000,refetchIntervalInBackground:updateOverlay,
  });
  const runner=updater.data;
  useEffect(()=>{if(runner?.running)setUpdateOverlay(true);},[runner?.running]);
  const cards=system.data?.cards||[];
  const updateCard=cards.find(c=>c.type==='update');
  async function refresh(){
    await Promise.all([client.invalidateQueries({queryKey:['system','model']}),client.invalidateQueries({queryKey:['system','runner']})]);
  }
  async function action(name:'check'|'logging'|'reload'|'update',level?:string){
    if(busy)return;
    setBusy(true);
    try {
      if(name==='check')await post<ApiResult>('/api/system/update-check');
      if(name==='logging')await post<ApiResult>('/api/system/logging',{log_level:level||'INFO'});
      if(name==='reload')await post<ApiResult>('/api/system/reload');
      if(name==='update'){
        const response=await post<ApiResult>('/api/system/self-update');
        setUpdateOverlay(true);
        notifications.show({color:'teal',message:response.message||'Update gestartet.'});
      }else notifications.show({color:'teal',message:name==='check'?'Updateprüfung abgeschlossen.':name==='reload'?'Runtime neu geladen.':'Log-Level aktualisiert.'});
      await refresh();
    }catch(e){failed(e);}finally{setBusy(false);setPending(null);}
  }
  async function importConfig(){
    if(!file||busy)return;
    setPending(null);setBusy(true);
    const form=new FormData();form.set('config_file',file);
    try{
      const result=await configJson<ApiResult>(fetch('/api/system/config/import',{method:'POST',body:form}));
      notifications.show({color:'teal',message:result.message||'Konfiguration importiert.'});
      setFile(null);await refresh();
    }catch(e){failed(e);}finally{setBusy(false);}
  }
  return <Stack gap="md">
    <Group justify="space-between" align="center">
      <div><Title order={2} size="h3">System</Title>
        <Text size="xs" c="dimmed">Betrieb, Host-System, Updates, Logs und Konfigurationssicherung</Text></div>
      <Group gap="xs"><Badge size="xs" color={system.error?'red':'gray'} variant="light">
        {system.error?'API-FEHLER':'API LIVE'}</Badge>
        <Button size="compact-sm" variant="subtle" color="gray" leftSection={<IconRefresh size={14}/>}
          disabled={busy} onClick={()=>void refresh()}>Aktualisieren</Button>
      </Group>
    </Group>
    {system.error&&<Alert color="red" icon={<IconAlertCircle size={17}/>}>{system.error.message}</Alert>}
    {cards.filter(c=>c.type==='update').map(card=><Stack gap="xs" key={card.id}>
      <UpdateCard card={card} busy={busy} onRefresh={()=>void action('check')}
        onStart={()=>setPending('update')} onLog={()=>setUpdateOverlay(true)}/>
      {import.meta.env.DEV&&<Group justify="flex-end">
        <Button variant="subtle" size="compact-sm" color="gray"
          onClick={()=>setPreviewOverlay(true)}>Update-Manager als Simulation ansehen</Button>
      </Group>}
    </Stack>)}
    {cards.filter(c=>c.type!=='update').map(card=>card.type==='backup'?<Card key={card.id}>
      <Stack gap="sm">
        <Text fw={650}>{card.title}</Text><Text c="dimmed" size="xs">{card.description}</Text>
        <Group gap="xs">
          <Button component="a" size="sm" variant="light" color="gray"
            href={card.export_url||'/system/config/export'} leftSection={<IconDownload size={16}/>}>
            Konfiguration herunterladen</Button>
        </Group>
        <Divider/>
        <FileInput label="Konfigurationsdatei importieren" placeholder="JSON-Datei auswählen"
          accept="application/json,.json" value={file} onChange={setFile}/>
        <Text size="xs" c="dimmed">Import ersetzt die Konfiguration und lädt die Runtime neu. Vorher unbedingt eine Sicherung herunterladen.</Text>
        <Group justify="flex-end"><Button color="orange" variant="light" size="sm" leftSection={<IconUpload size={15}/>}
          disabled={busy||!file} onClick={()=>setPending('import')}>Konfiguration importieren</Button></Group>
      </Stack>
    </Card>:card.type==='logs'?<LiveLogs key={card.id} card={card} busy={busy}
      onLogLevel={async level=>{await action('logging',level);}}/>
    :<Card key={card.id}>
      <Stack gap="sm"><Text fw={650}>{card.title}</Text>
        {card.description&&<Text c="dimmed" size="xs">{card.description}</Text>}
        <Rows rows={card.rows}/>
        {!!card.actions?.length&&<Group justify="flex-end" gap="xs">
          {card.actions.map(a=><Button key={a.id} size="sm" variant="light"
            color={a.style==='warning'?'orange':'gray'} disabled={busy}
            onClick={()=>{if(a.id==='reload_runtime')setPending('reload');}}>
            {a.label}</Button>)}
        </Group>}
      </Stack>
    </Card>)}
    <Modal opened={!!pending} onClose={()=>setPending(null)} centered title="Aktion bestätigen">
      <Stack gap="md">
        <Text size="sm">{pending==='import'
          ?'Die ausgewählte Konfiguration ersetzt alle aktuellen Einstellungen und lädt den Controller neu. Hast du vorher eine Sicherung erstellt?'
          :pending==='reload'?'PV2Hash-Runtime wirklich neu laden?'
            :`PV2Hash wirklich auf ${updateCard?.model?.update_status?.release_version_full||'das aktuelle Release'} aktualisieren? Der Dienst wird dabei neu gestartet.`}</Text>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" disabled={busy} onClick={()=>setPending(null)}>Abbrechen</Button>
          <Button color={pending==='reload'?'violet':'orange'} loading={busy}
            onClick={()=>{if(pending==='import')void importConfig();else void action(pending==='update'?'update':'reload');}}>
            Bestätigen</Button>
        </Group>
      </Stack>
    </Modal>
    <UpdateOverlay opened={updateOverlay} onClose={()=>setUpdateOverlay(false)}
      runner={runner} connecting={updater.isError}/>
    <UpdateOverlay opened={previewOverlay} onClose={()=>setPreviewOverlay(false)} preview/>
  </Stack>;
}

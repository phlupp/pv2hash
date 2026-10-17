import {useState,useEffect} from 'react';
import {
  Anchor, Badge, Button, Card, Divider, Group, Modal, SimpleGrid, Stack, Text, TextInput,
  Select, Checkbox,
} from '@mantine/core';
import {IconChevronDown,IconChevronRight,IconDeviceFloppy,IconTrash,IconPower,IconRefresh} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';
import {useQueryClient} from '@tanstack/react-query';
import {QualityBadge} from '../dashboard/components/QualityBadge';
import {minerWebGuiUrl} from '../dashboard/minerLink';
import {saveSocket,deleteSocket,switchSocket} from './api';
import {SocketFields} from './SocketFields';
import {
  initialSocketValues,serializeSocketValues,validateSocket,
  type SocketConfig,type SocketDriver,type SocketLive,type SocketFormValues,
} from './model';

function statusLabel(s?:SocketLive):string {
  if(!s)return 'Status unbekannt';
  if(!s.monitor_enabled)return 'Verbindung aus';
  if(!s.reachable)return 'Nicht erreichbar';
  return s.is_on===true?'Ein':s.is_on===false?'Aus':'Unbekannt';
}

export function SocketCard({
  config,driver,drivers,live,expanded,onExpandedChange,
}:{
  config:SocketConfig;driver:SocketDriver;drivers:SocketDriver[];
  live?:SocketLive;expanded:boolean;onExpandedChange:(next:boolean)=>void;
}){
  const client=useQueryClient();
  const [values,setValues]=useState<SocketFormValues>(()=>initialSocketValues(driver,config));
  const [dirty,setDirty]=useState(false);
  const [busy,setBusy]=useState(false);
  const [confirm,setConfirm]=useState<'off'|'reboot'|'delete'|null>(null);
  const currentDriver=drivers.find(d=>d.id===values.driver)||driver;
  useEffect(()=>{
    if(dirty)return;
    setValues(initialSocketValues(driver,config));
  },[driver,config,dirty]);

  const isOn=live?.is_on===true;
  const active=live?.reachable===true;
  const assignment=config.assignment;
  function change(name:string,value:string|boolean){
    if(name==='driver'){
      const next=drivers.find(x=>x.id===value);
      if(!next)return;
      // Preserve identity; use new driver's own default settings.
      setValues({
        ...initialSocketValues(next),
        name:values.name,host:values.host,priority:values.priority,
        monitor_enabled:values.monitor_enabled,control_enabled:values.control_enabled,
      });
    }else setValues(old=>({...old,[name]:value}));
    setDirty(true);
  }
  async function refresh(){
    await Promise.all([
      client.invalidateQueries({queryKey:['sockets','gui']}),
      client.invalidateQueries({queryKey:['sockets','status']}),
      client.invalidateQueries({queryKey:['dashboard']}),
    ]);
  }
  async function save(){
    if(busy||!dirty)return;
    const error=validateSocket(currentDriver,values);
    if(error){notifications.show({message:error,color:'red'});return;}
    setBusy(true);
    try {
      const x=await saveSocket(config.id,serializeSocketValues(currentDriver,values,true));
      notifications.show({message:x.message||'Socket gespeichert.',color:'teal'});
      setDirty(false);
      await refresh();
    }catch(e){notifications.show({message:e instanceof Error?e.message:'Speichern fehlgeschlagen.',color:'red'});}
    finally{setBusy(false);}
  }
  async function perform(action:'on'|'off'|'reboot'|'delete'){
    if(busy)return;
    setConfirm(null);
    setBusy(true);
    try {
      const x=action==='delete'?await deleteSocket(config.id):await switchSocket(config.id,action);
      notifications.show({message:x.message||'Socket-Aktion erfolgreich.',color:'teal'});
      await refresh();
    }catch(e){notifications.show({message:e instanceof Error?e.message:'Socket-Aktion fehlgeschlagen.',color:'red'});}
    finally{setBusy(false);}
  }
  const settingsChangedDriver=currentDriver.id!==config.driver;
  const webGui=config.driver==='tasmota_http'&&config.host.trim()
    ? minerWebGuiUrl(/^https?:\/\//i.test(config.host)?config.host:`http://${config.host}`)
    : null;
  return <Card component="section" aria-label={config.name} padding="md">
    <Stack gap="sm">
      <Group justify="space-between" align="flex-start" gap="xs">
        <div style={{minWidth:0,flex:1}}>
          <Group gap="xs" wrap="wrap">
            <Text fw={650}>{config.name}</Text>
            <Badge color={active&&isOn?'teal':'gray'} size="xs" variant="light">{statusLabel(live)}</Badge>
            <QualityBadge quality={live?.quality} source={driver.label} measured={!!live} />
            {dirty&&<Badge color="orange" variant="light" size="xs">Ungespeichert</Badge>}
          </Group>
          <Group gap="xs" mt={4}>
            <Text size="xs" c="dimmed">{driver.label}</Text>
            {webGui&&<Anchor href={webGui} target="_blank" rel="noopener noreferrer" size="xs">WebGUI ↗</Anchor>}
          </Group>
        </div>
        <Button size="compact-sm" variant="subtle" color="gray" aria-expanded={expanded}
          rightSection={expanded?<IconChevronDown size={16}/>:<IconChevronRight size={16}/>}
          onClick={()=>onExpandedChange(!expanded)}>{expanded?'Schließen':'Details'}</Button>
      </Group>
      <Group gap={6} wrap="wrap">
        <Badge size="sm" color="gray" variant="light">{live?.power_w==null?'—':Math.round(live.power_w).toLocaleString('de-DE')+' W'}</Badge>
        <Badge size="sm" color="gray" variant="outline">Prio {config.priority}</Badge>
        {assignment?.target_id&&<Badge size="sm" color="violet" variant="light">Miner: {assignment.target_name||assignment.target_id}</Badge>}
      </Group>
      {expanded&&<Stack gap="sm">
        <Divider label="Identität und Steuerung" labelPosition="left"/>
        <SimpleGrid cols={{base:1,sm:2,md:4}} spacing="xs">
          <Select label="Treiber" data={drivers.map(d=>({value:d.id,label:d.label}))} value={currentDriver.id}
            onChange={v=>{if(v)change('driver',v);}} allowDeselect={false} disabled={busy}/>
          <TextInput label="Name" value={String(values.name??'')} disabled={busy}
            onChange={e=>change('name',e.currentTarget.value)}/>
          <TextInput label="Host / IP" value={String(values.host??'')} disabled={busy}
            onChange={e=>change('host',e.currentTarget.value)}/>
          <TextInput label="Priorität" type="number" value={String(values.priority??100)}
            disabled={busy} onChange={e=>change('priority',e.currentTarget.value)}/>
        </SimpleGrid>
        <Group gap="lg">
          <Checkbox label="Verbindung" checked={values.monitor_enabled===true} disabled={busy}
            onChange={e=>change('monitor_enabled',e.currentTarget.checked)}/>
          <Checkbox label="Automatik" checked={values.control_enabled===true} disabled={busy}
            onChange={e=>change('control_enabled',e.currentTarget.checked)}/>
        </Group>
        {settingsChangedDriver&&<Text size="xs" c="dimmed">Treiberwechsel vorgemerkt – erst nach Speichern aktiv.</Text>}
        <Divider label={currentDriver.label} labelPosition="left"/>
        <SocketFields driver={currentDriver} values={values} disabled={busy}
          configuredPassword={config.password_configured&&!settingsChangedDriver} onChange={change}/>
        <Group justify="flex-end">
          {dirty&&<Button variant="subtle" color="gray" size="sm" disabled={busy}
            onClick={()=>{setValues(initialSocketValues(driver,config));setDirty(false);}}>Verwerfen</Button>}
          <Button leftSection={<IconDeviceFloppy size={16}/>} size="sm" disabled={!dirty} loading={busy}
            onClick={()=>void save()}>Speichern</Button>
        </Group>
        {live?.last_error&&<Text size="xs" c="red">Letzter Fehler: {live.last_error}</Text>}
        {!!Object.keys(live?.details||{}).length&&<>
          <Divider label="Gerätedetails" labelPosition="left"/>
          <SimpleGrid cols={{base:2,sm:3,lg:5}} spacing="xs">
            {Object.entries(live?.details||{}).map(([name,value])=><div key={name}>
              <Text size="xs" c="dimmed">{name}</Text>
              <Text size="sm" fw={600}>{String(value??'—')}</Text>
            </div>)}
          </SimpleGrid>
        </>}
        <Divider label="Manuelle Aktionen" labelPosition="left"/>
        <Group gap="xs" justify="space-between" wrap="wrap">
          <Group gap="xs">
            <Button size="sm" variant="light" color="gray" leftSection={<IconPower size={15}/>}
              loading={busy} onClick={()=>void perform('on')}>Einschalten</Button>
            <Button size="sm" variant="light" color="gray" loading={busy}
              onClick={()=>setConfirm('off')}>Ausschalten</Button>
            {config.driver==='tasmota_http'&&<Button size="sm" variant="light" color="gray"
              leftSection={<IconRefresh size={15}/>} disabled={busy}
              onClick={()=>setConfirm('reboot')}>Neustarten</Button>}
          </Group>
          <Button size="sm" variant="subtle" color="red" leftSection={<IconTrash size={15}/>}
            disabled={busy} onClick={()=>setConfirm('delete')}>Löschen</Button>
        </Group>
      </Stack>}
    </Stack>
    <Modal opened={confirm!==null} onClose={()=>setConfirm(null)} title="Socket-Aktion bestätigen" centered size="sm">
      <Stack gap="md">
        <Text size="sm">{confirm==='delete'?`Socket „${config.name}“ wirklich löschen?`:
          confirm==='off'?`Socket „${config.name}“ wirklich ausschalten? Angeschlossene Geräte verlieren möglicherweise die Stromversorgung.`:
            `Socket „${config.name}“ wirklich neu starten?`}</Text>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={()=>setConfirm(null)}>Abbrechen</Button>
          <Button color={confirm==='delete'?'red':'violet'} onClick={()=>{if(confirm)void perform(confirm);}}>Bestätigen</Button>
        </Group>
      </Stack>
    </Modal>
  </Card>;
}

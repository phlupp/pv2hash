import { useState } from 'react';
import {
  Button, Card, Divider, Group, Select, SimpleGrid,
  Stack, Text, TextInput,
} from '@mantine/core';
import {IconPlus,IconSearch} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';
import {SocketFields} from './SocketFields';
import {addSocket,discoverSockets} from './api';
import {
  initialSocketValues,serializeSocketValues,validateSocket,
  type DiscoveryResult,type NetworkInterface,type SocketDriver,type SocketFormValues,
} from './model';

export function SocketCreate({
  drivers,interfaces,busy,onBusy,onCreated,
}:{
  drivers:SocketDriver[];interfaces:NetworkInterface[];
  busy:boolean;onBusy:(v:boolean)=>void;onCreated:(id?:string)=>Promise<void>;
}){
  const [open,setOpen]=useState(false);
  const [driverId,setDriverId]=useState(drivers[0]?.id||'simulator');
  const driver=drivers.find(d=>d.id===driverId)||drivers[0];
  const [values,setValues]=useState<SocketFormValues>(()=>driver?initialSocketValues(driver):{});
  const [ifname,setIfname]=useState('');
  const [cidr,setCidr]=useState('');
  const [results,setResults]=useState<DiscoveryResult[]|null>(null);
  const [scanned,setScanned]=useState<string[]>([]);
  const [searching,setSearching]=useState(false);

  if(!driver)return <Text c="dimmed">Keine Socket-Treiber verfügbar.</Text>;
  function change(name:string,value:string|boolean){
    if(name==='driver'){
      const d=drivers.find(x=>x.id===value);
      if(!d)return;
      setDriverId(d.id);
      setValues(initialSocketValues(d));
      setResults(null);
    }else setValues(old=>({...old,[name]:value}));
  }
  async function create(){
    if(busy)return;
    const err=validateSocket(driver,values);
    if(err){notifications.show({color:'red',message:err});return;}
    onBusy(true);
    try {
      const data=await addSocket(serializeSocketValues(driver,values,false));
      notifications.show({color:'teal',message:data.message||'Socket angelegt.'});
      setValues(initialSocketValues(driver));
      setResults(null);
      setOpen(false);
      await onCreated(data.socket_id);
    }catch(e){notifications.show({color:'red',message:e instanceof Error?e.message:'Socket konnte nicht angelegt werden.'});}
    finally{onBusy(false);}
  }
  async function discover(){
    if(searching||busy)return;
    if(!ifname&&!cidr.trim()){
      notifications.show({color:'orange',message:'Bitte zuerst ein Netzwerk-Interface oder Suchnetz wählen.'});return;
    }
    const form=new FormData();
    if(ifname)form.set('ifname',ifname);
    if(cidr.trim())form.set('cidr',cidr.trim());
    form.set('port',String(values.port||80));
    setSearching(true);
    setResults(null);
    try {
      const x=await discoverSockets(form);
      setResults(x.results);
      setScanned(x.cidrs||[]);
      notifications.show({color:'teal',message:`${x.results.length} Tasmota-Geräte gefunden.`});
    }catch(e){notifications.show({color:'red',message:e instanceof Error?e.message:'Tasmota-Suche fehlgeschlagen.'});}
    finally{setSearching(false);}
  }

  return <Card>
    <Stack gap="sm">
      <Group justify="space-between" gap="xs">
        <div>
          <Text fw={650}>Socket anlegen</Text>
          {open&&<Text c="dimmed" size="xs">Simulator oder Tasmota HTTP · bei Bedarf Tasmota-Geräte im LAN suchen</Text>}
        </div>
        <Button variant={open?'subtle':'filled'} color={open?'gray':'violet'} size="sm"
          leftSection={!open?<IconPlus size={16}/>:undefined} disabled={busy}
          onClick={()=>setOpen(v=>!v)}>{open?'Schließen':'Socket hinzufügen'}</Button>
      </Group>
      {open&&<Stack gap="sm">
        <SimpleGrid cols={{base:1,sm:2,md:4}} spacing="xs">
          <Select label="Treiber" value={driver.id} allowDeselect={false} disabled={busy}
            data={drivers.map(d=>({value:d.id,label:d.label}))}
            onChange={v=>{if(v)change('driver',v);}}/>
          <TextInput label="Name" disabled={busy} value={String(values.name??'')}
            onChange={e=>change('name',e.currentTarget.value)}/>
          <TextInput label="Host / IP" disabled={busy} value={String(values.host??'')}
            onChange={e=>change('host',e.currentTarget.value)}/>
        </SimpleGrid>
        <Divider label={driver.label} labelPosition="left"/>
        <SocketFields driver={driver} values={values} disabled={busy} onChange={change}/>
        {driver.id==='tasmota_http'&&<>
          <Divider label="Tasmota im Netzwerk suchen" labelPosition="left"/>
          <Text c="dimmed" size="xs">Die Suche wird nur manuell gestartet. Host/IP kann auch direkt eingegeben werden.</Text>
          <SimpleGrid cols={{base:1,sm:2}} spacing="xs">
            <Select label="Netzwerk-Interface" clearable value={ifname||null}
              data={interfaces.map(x=>({value:x.ifname,label:x.label}))}
              placeholder="Bitte auswählen"
              onChange={v=>setIfname(v||'')} disabled={busy||searching}/>
            <TextInput label="Suchnetz überschreiben (optional)" placeholder="192.168.10.0/23"
              value={cidr} onChange={e=>setCidr(e.currentTarget.value)} disabled={busy||searching}/>
          </SimpleGrid>
          <Group gap="sm" align="center">
            <Button size="sm" variant="light" color="gray" leftSection={<IconSearch size={15}/>}
              loading={searching} disabled={busy} onClick={()=>void discover()}>Tasmota-Geräte suchen</Button>
            {!!scanned.length&&<Text size="xs" c="dimmed">Gescannt: {scanned.join(', ')}</Text>}
          </Group>
          {results&&(!results.length?<Text c="dimmed" size="sm">Keine Tasmota-Geräte gefunden. Host/IP kann manuell eingetragen werden.</Text>
            :<Stack gap={4}>
              {results.map((item,i)=><Button key={item.host+'-'+i} fullWidth size="sm"
                disabled={!!item.existing_socket_id||busy} variant="light" color="gray"
                onClick={()=>{
                  setValues(old=>({...old,host:item.host,name:item.name||item.device_name||item.friendly_name||old.name,port:String(item.port||80)}));
                }}
              >{item.host} · {item.name||item.device_name||item.friendly_name||'Tasmota'}{item.existing_socket_id?' · Bereits angelegt':''}</Button>)}
            </Stack>)}
        </>}
        <Group justify="flex-end">
          <Button color="gray" variant="subtle" onClick={()=>setOpen(false)} disabled={busy}>Abbrechen</Button>
          <Button size="sm" leftSection={<IconPlus size={16}/>} loading={busy} onClick={()=>void create()}>Socket anlegen</Button>
        </Group>
      </Stack>}
    </Stack>
  </Card>;
}

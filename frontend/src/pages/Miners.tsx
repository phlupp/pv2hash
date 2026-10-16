import { useState } from 'react';
import {
  Alert, Badge, Button, Card, Divider, Group, Loader, Select, Stack, Text, Title,
} from '@mantine/core';
import { IconAlertCircle, IconPlus, IconRefresh } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { addMiner, loadMinerGui, loadMinerLive } from '../miners/api';
import { MinerCard } from '../miners/MinerCard';
import { MinerFields } from '../miners/fields';
import { fieldsToValues, formForFields, validateFields, type MinerValues } from '../miners/model';

export function Miners() {
  const client=useQueryClient();
  const [expandedIds,setExpandedIds]=useState<string[]>([]);
  const gui=useQuery({queryKey:['miners','gui'],queryFn:loadMinerGui,staleTime:10_000});
  const live=useQuery({
    queryKey:['miners','status',expandedIds],queryFn:()=>loadMinerLive(expandedIds),
    refetchInterval:5_000,refetchIntervalInBackground:false,
  });
  const [creating,setCreating]=useState(false);
  const [driver,setDriver]=useState('simulator');
  const [createValues,setCreateValues]=useState<MinerValues>({});
  const [busy,setBusy]=useState(false);
  const catalog=gui.data?.driver_catalog??[];
  const current=catalog.find(x=>x.key===driver)??catalog[0];
  const createFields=[
    ...(gui.data?.core_identity_basic_fields??[]),
    ...(current?.basic_fields??[]),
  ];
  const options=catalog.map(x=>({value:x.key,label:x.label+(x.supports_gui_schema?'':' (nicht unterstützt)')}));
  const liveById=new Map((live.data?.miners??[]).map(m=>[m.id,m.summary]));

  function switchDriver(next:string){
    const target=catalog.find(x=>x.key===next);
    if(!target)return;
    setDriver(next);
    setCreateValues(fieldsToValues([...(gui.data?.core_identity_basic_fields??[]),...target.basic_fields]));
  }
  function openCreate() {
    setDriver(current?.key??'simulator');
    setCreateValues(fieldsToValues(createFields));
    setCreating(true);
  }
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({queryKey:['miners','gui']}),
      client.invalidateQueries({queryKey:['miners','status']}),
      client.invalidateQueries({queryKey:['dashboard']}),
    ]);
  }
  async function create() {
    if(!current?.supports_gui_schema||busy)return;
    const error=validateFields(createFields,createValues,false);
    if(error){notifications.show({color:'red',message:error});return;}
    setBusy(true);
    try{
      const form=formForFields(createFields,createValues);
      form.set('driver',current.key);
      const response=await addMiner(form);
      notifications.show({color:'teal',message:response.message||'Miner angelegt.'});
      setCreating(false);
      if(response.miner_id)setExpandedIds(prev=>[...new Set([...prev,response.miner_id as string])]);
      setCreateValues({});
      await refresh();
    }catch(e){notifications.show({color:'red',message:e instanceof Error?e.message:'Miner konnte nicht angelegt werden.'});}
    finally{setBusy(false);}
  }

  return <Stack gap="md">
    <Group justify="space-between" align="center">
      <div>
        <Title order={2} size="h3">Miner</Title>
        <Text size="xs" c="dimmed">Miner verwalten, Leistungsprofile und Treibereinstellungen</Text>
      </div>
      <Group gap="xs">
        <Badge color={live.isError?'red':'gray'} variant="light" size="xs">
          {live.isError?'API OFFLINE':live.isPending?'Verbinde …':'LIVE'}
        </Badge>
        <Button size="compact-sm" color="gray" variant="subtle" leftSection={<IconRefresh size={15}/>}
          onClick={()=>void refresh()}>Aktualisieren</Button>
      </Group>
    </Group>
    {(gui.error||live.error)&&<Alert color="red" title="Miner-Daten nicht erreichbar"
      icon={<IconAlertCircle size={16}/>}>{gui.error?.message||live.error?.message}</Alert>}

    <Card>
      {!creating ? <Group justify="space-between">
        <Text fw={600}>Neuen Miner anlegen</Text>
        <Button size="sm" leftSection={<IconPlus size={16}/>}
          onClick={openCreate} disabled={!gui.data}>Miner hinzufügen</Button>
      </Group> : <Stack gap="sm">
        <Group justify="space-between">
          <Text fw={600}>Neuen Miner anlegen</Text>
          <Button color="gray" variant="subtle" size="compact-sm" onClick={()=>setCreating(false)}
            disabled={busy}>Schließen</Button>
        </Group>
        <Text size="xs" c="dimmed">Zuerst Treiber und Grunddaten auswählen. Alle weiteren Einstellungen stehen danach in der Miner-Karte zur Verfügung.</Text>
        <Select label="Treiber" size="sm" data={options} value={current?.key||null}
          onChange={v=>{if(v)switchDriver(v);}} allowDeselect={false}/>
        {current?.supports_gui_schema ? <>
          <Divider label="Grunddaten" labelPosition="left"/>
          <MinerFields fields={createFields} values={createValues} existing={false} disabled={busy}
            onChange={(field,value)=>setCreateValues(old=>({...old,[field.name]:value}))}/>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={()=>setCreating(false)} disabled={busy}>Abbrechen</Button>
            <Button loading={busy} leftSection={<IconPlus size={16}/>}
              onClick={()=>void create()}>Miner anlegen</Button>
          </Group>
        </> : <Text size="xs" c="dimmed">Dieser Treiber hat noch kein unterstütztes Konfigurationsschema.</Text>}
      </Stack>}
    </Card>
    {gui.isPending ? <Group py="lg"><Loader size="sm"/><Text c="dimmed">Miner-Modelle werden geladen …</Text></Group> : (
      <Stack gap="sm">
        {(gui.data?.miners??[]).map(miner=><MinerCard
          key={miner.id} model={miner} live={liveById.get(miner.id)}
          liveDetails={live.data?.miners.find(item=>item.id===miner.id)?.details}
          expanded={expandedIds.includes(miner.id)}
          onExpandedChange={next=>setExpandedIds(prev=>next
            ? [...new Set([...prev,miner.id])] : prev.filter(id=>id!==miner.id))}/>)}
        {!gui.data?.miners?.length && <Text c="dimmed" size="sm">Noch keine Miner angelegt.</Text>}
      </Stack>
    )}
  </Stack>;
}

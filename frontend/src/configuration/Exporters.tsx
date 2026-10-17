import {useState} from 'react';
import {Badge,Button,Card,Group,Modal,Select,Stack,Text,TextInput,Checkbox} from '@mantine/core';
import {IconPlus,IconTrash} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {deleteExporter,getExporters,getExporterStatus,saveExporter,testExporter} from './api';
import {ConfigFields} from './ConfigFields';
import {fieldValues,validateConfig,type ConfigValues,type ExporterEntry,type ExporterType} from './types';

const message=(e:unknown)=>notifications.show({color:'red',message:e instanceof Error?e.message:'Aktion fehlgeschlagen.'});
export function Exporters(){
  const client=useQueryClient();
  const config=useQuery({queryKey:['exporters','config'],queryFn:getExporters});
  const status=useQuery({queryKey:['exporters','status'],queryFn:getExporterStatus,refetchInterval:30000});
  const [editing,setEditing]=useState<string|null>(null);
  const [kind,setKind]=useState('');
  const [name,setName]=useState('');
  const [values,setValues]=useState<ConfigValues>({});
  const [enabled,setEnabled]=useState(false);
  const [busy,setBusy]=useState(false);
  const [remove,setRemove]=useState<ExporterEntry|null>(null);
  const types=config.data?.types||[];
  const selected=types.find(t=>t.type===kind)||types[0];
  const entry=config.data?.exporters.find(e=>e.id===editing);
  function open(current?:ExporterEntry){
    const next=types.find(t=>t.type===current?.type)||types[0];
    if(!next)return;
    setKind(next.type);setName(current?.name||'');
    setValues(fieldValues((current?.schema?.fields||next.fields).map(f=>({...f,
      value:f.type==='password'?'':f.value}))));
    setEnabled(Boolean(current?.enabled));setEditing(current?.id||'new');
  }
  function changeType(type:string) {
    const next=types.find(t=>t.type===type);if(!next)return;
    setKind(type);setValues(fieldValues(next.fields));setEnabled(false);
  }
  async function reload(){
    await Promise.all([client.invalidateQueries({queryKey:['exporters','config']}),
      client.invalidateQueries({queryKey:['exporters','status']})]);
  }
  async function save(){
    if(!selected||busy)return;
    if(!name.trim()){notifications.show({color:'red',message:'Bitte Exportzielnamen eingeben.'});return;}
    const error=validateConfig(selected.fields,values,Boolean(entry));
    if(error){notifications.show({color:'red',message:error});return;}
    setBusy(true);
    try{
      await saveExporter({id:entry?.id,type:selected.type,name,enabled:entry?.test_ok===true&&enabled,settings:values});
      notifications.show({color:'teal',message:'Exportziel gespeichert.'});
      setEditing(null);await reload();
    }catch(e){message(e);}finally{setBusy(false);}
  }
  async function runTest(id:string){
    if(busy)return;setBusy(true);
    try{const x=await testExporter(id);notifications.show({color:'teal',message:x.message||'Verbindungstest erfolgreich.'});await reload();}
    catch(e){message(e);}finally{setBusy(false);}
  }
  async function removeConfirmed(){
    if(!remove||busy)return;setBusy(true);
    try{await deleteExporter(remove.id);notifications.show({color:'teal',message:'Exportziel gelöscht.'});setRemove(null);setEditing(null);await reload();}
    catch(e){message(e);}finally{setBusy(false);}
  }
  return <Card>
    <Stack gap="sm">
      <Group justify="space-between" gap="xs">
        <div><Text fw={650}>Datenexporte</Text>
          <Text c="dimmed" size="xs">Unabhängige Exportziele, Felddefinitionen aus den Exporter-Adaptern</Text></div>
        <Button size="compact-sm" variant="light" leftSection={<IconPlus size={15}/>} disabled={busy||!types.length}
          onClick={()=>open()}>Exportziel hinzufügen</Button>
      </Group>
      {config.error&&<Text c="red" size="sm">{config.error.message}</Text>}
      {(config.data?.exporters||[]).map(item=>{
        const run=status.data?.exporters.find(x=>x.id===item.id);
        const streams=Object.values(run?.streams||{});
        const error=streams.find(x=>x.last_error)?.last_error;
        const success=streams.map(x=>x.last_success_at).filter(Boolean).sort().at(-1);
        return <Group key={item.id} justify="space-between" gap="xs" p="xs" bg="dark.7" style={{borderRadius:4}}>
          <div style={{minWidth:0,flex:1}}>
            <Group gap="xs"><Text size="sm" fw={600}>{item.name}</Text>
              <Badge variant="light" size="xs" color={item.enabled?'teal':'gray'}>{item.enabled?'Aktiv':'Inaktiv'}</Badge>
              <Badge variant="outline" color={item.test_ok?'teal':'gray'} size="xs">{item.test_ok?'Verbindung geprüft':'Nicht geprüft'}</Badge>
            </Group>
            <Text size="xs" c="dimmed">{item.schema.label||item.type} · {error?'Fehler: '+error:success?'Letzter Export: '+new Date(success).toLocaleString('de-DE'):'Noch keine Übertragung'}</Text>
          </div>
          <Group gap={5}>
            <Button size="compact-sm" variant="subtle" color="gray" onClick={()=>open(item)}>Bearbeiten</Button>
            <Button size="compact-sm" variant="subtle" color="gray" disabled={busy}
              onClick={()=>void runTest(item.id)}>Verbindung testen</Button>
            <Button size="compact-sm" color="red" variant="subtle" disabled={busy}
              onClick={()=>setRemove(item)} leftSection={<IconTrash size={13}/>}>Löschen</Button>
          </Group>
        </Group>;
      })}
      {config.data&&!config.data.exporters.length&&<Text c="dimmed" size="sm">Noch keine Exportziele eingerichtet.</Text>}
      {editing&&selected&&<Stack gap="sm" pt="sm">
        <Text fw={600} size="sm">{entry?'Exportziel bearbeiten':'Neues Exportziel'}</Text>
        <Group align="flex-start" gap="xs" grow>
          <TextInput label="Name" value={name} maxLength={80} required onChange={e=>setName(e.currentTarget.value)}/>
          <Select label="Typ" value={selected.type} allowDeselect={false} disabled={!!entry}
            data={types.map(x=>({value:x.type,label:x.label}))} onChange={v=>{if(v)changeType(v);}}/>
        </Group>
        <ConfigFields fields={selected.fields} values={values} busy={busy} existing={!!entry}
          onChange={(n,v)=>setValues(x=>({...x,[n]:v}))}/>
        <Checkbox label="Export aktivieren" checked={enabled} disabled={busy||!entry?.test_ok}
          description={!entry?.test_ok?'Erst speichern und die Verbindung erfolgreich testen.':undefined}
          onChange={e=>setEnabled(e.currentTarget.checked)}/>
        <Group justify="flex-end">
          <Button size="sm" variant="subtle" color="gray" disabled={busy} onClick={()=>setEditing(null)}>Abbrechen</Button>
          <Button size="sm" loading={busy} onClick={()=>void save()}>Exportziel speichern</Button>
        </Group>
      </Stack>}
    </Stack>
    <Modal opened={!!remove} centered title="Exportziel löschen?" onClose={()=>setRemove(null)}>
      <Stack><Text size="sm">„{remove?.name}“ wirklich entfernen?</Text><Group justify="flex-end">
        <Button variant="subtle" color="gray" onClick={()=>setRemove(null)}>Abbrechen</Button>
        <Button color="red" loading={busy} onClick={()=>void removeConfirmed()}>Löschen</Button>
      </Group></Stack>
    </Modal>
  </Card>;
}

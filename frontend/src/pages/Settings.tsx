import {useEffect,useState} from 'react';
import {Alert,Badge,Button,Card,Group,Loader,Stack,Text,Title} from '@mantine/core';
import {IconMapPin,IconDeviceFloppy,IconRefresh} from '@tabler/icons-react';
import {notifications} from '@mantine/notifications';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {getSettings,geocodeLocation,saveSettings} from '../configuration/api';
import {ConfigFields} from '../configuration/ConfigFields';
import {Exporters} from '../configuration/Exporters';
import {fieldValues,validateConfig,type ConfigValues} from '../configuration/types';

const fail=(error:unknown)=>notifications.show({color:'red',message:error instanceof Error?error.message:'Aktion fehlgeschlagen.'});
export function Settings(){
  const client=useQueryClient();
  const query=useQuery({queryKey:['settings','model'],queryFn:getSettings});
  const [values,setValues]=useState<ConfigValues>({});
  const [dirty,setDirty]=useState(false);
  const [busy,setBusy]=useState(false);
  const [geocoding,setGeocoding]=useState(false);
  useEffect(()=>{
    if(query.data&&!dirty){
      setValues(fieldValues(query.data.model.sections.flatMap(s=>s.fields)));
    }
  },[query.data,dirty]);
  useEffect(()=>{
    const listener=(e:BeforeUnloadEvent)=>{if(dirty)e.preventDefault();};
    window.addEventListener('beforeunload',listener);
    return ()=>window.removeEventListener('beforeunload',listener);
  },[dirty]);
  const sections=query.data?.model.sections||[];
  async function save(){
    if(busy||!dirty)return;
    const error=validateConfig(sections.flatMap(s=>s.fields),values);
    if(error){notifications.show({color:'red',message:error});return;}
    setBusy(true);
    try{
      const result=await saveSettings(values);
      setValues(fieldValues(result.model.sections.flatMap(s=>s.fields)));
      setDirty(false);
      client.setQueryData(['settings','model'],result);
      await Promise.all([
        client.invalidateQueries({queryKey:['status']}),
        client.invalidateQueries({queryKey:['dashboard']}),
      ]);
      notifications.show({color:'teal',message:result.message||'Einstellungen gespeichert.'});
    }catch(e){fail(e);}finally{setBusy(false);}
  }
  async function findLocation(){
    if(geocoding)return;
    const address=String(values.location_address||'').trim();
    if(!address){notifications.show({color:'orange',message:'Bitte zunächst eine Adresse eingeben.'});return;}
    setGeocoding(true);
    try{
      const {location}=await geocodeLocation(address);
      setValues(v=>({...v,location_lat:String(location.lat),location_lon:String(location.lon)}));
      setDirty(true);
      notifications.show({color:'teal',message:'Koordinaten ermittelt. Bitte Einstellungen speichern.'});
    }catch(e){fail(e);}finally{setGeocoding(false);}
  }
  function discard(){
    if(!query.data)return;
    setValues(fieldValues(query.data.model.sections.flatMap(s=>s.fields)));
    setDirty(false);
  }
  return <Stack gap="md">
    <Group justify="space-between" align="center">
      <div><Title order={2} size="h3">Einstellungen</Title>
        <Text c="dimmed" size="xs">Basis-Konfiguration für Instanz, Regelung und Datenexport</Text></div>
      <Group gap="xs">
        {dirty&&<Badge color="orange" variant="light" size="sm">Ungespeichert</Badge>}
        <Button variant="subtle" color="gray" size="compact-sm" disabled={busy||dirty}
          title={dirty?'Bitte zuerst speichern oder Änderungen verwerfen.':'Einstellungen neu laden'}
          leftSection={<IconRefresh size={15}/>} onClick={()=>void query.refetch()}>Aktualisieren</Button>
      </Group>
    </Group>
    {query.error&&<Alert color="red">{query.error.message}</Alert>}
    {query.isPending?<Group><Loader size="sm"/><Text size="sm">Einstellungen werden geladen …</Text></Group>
    :sections.map(section=><Card key={section.id} padding="md">
      <Stack gap="sm">
        <div><Text fw={650}>{section.title}</Text>
          {(section.subtitle||section.description)&&<Text size="xs" c="dimmed">{section.subtitle||section.description}</Text>}</div>
        <ConfigFields fields={section.fields} values={values} busy={busy}
          onChange={(n,v)=>{setValues(old=>({...old,[n]:v}));setDirty(true);}}/>
        {section.id==='location'&&<Group justify="flex-end">
          <Button variant="light" color="gray" size="sm" leftSection={<IconMapPin size={15}/>}
            loading={geocoding} disabled={busy} onClick={()=>void findLocation()}>
            Koordinaten aus Adresse ermitteln
          </Button>
        </Group>}
      </Stack>
    </Card>)}
    {query.data&&<Card padding="sm">
      <Group justify="flex-end" gap="xs">
        {dirty&&<Button variant="subtle" color="gray" size="sm" disabled={busy}
          onClick={discard}>Verwerfen</Button>}
        <Button leftSection={<IconDeviceFloppy size={16}/>} size="sm" loading={busy}
          disabled={!dirty} onClick={()=>void save()}>Einstellungen speichern</Button>
      </Group>
    </Card>}
    <Exporters/>
  </Stack>;
}
